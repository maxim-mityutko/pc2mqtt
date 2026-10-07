"""Windows desktop-session tray UI; MQTT and sensors run on a worker thread."""

import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
from threading import Event, Thread


def configure_logging():
    directory = Path(os.environ['LOCALAPPDATA']) / 'pc2mqtt'
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / 'pc2mqtt.log'
    handler = RotatingFileHandler(path, maxBytes=1024 * 1024, backupCount=2, encoding='utf-8')
    handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(name)s: %(message)s'))
    logging.basicConfig(level=logging.INFO, handlers=[handler], force=True)
    return path


class WindowsTray:
    def __init__(self, pc, log_path):
        # Lazy imports keep Linux independent of the GUI packages and display server.
        import pystray
        from PIL import Image, ImageDraw

        self.pc = pc
        self.log_path = log_path
        self.stopped = Event()
        self.failed = False
        self.worker = Thread(target=self._work, name='pc2mqtt-sensors', daemon=True)
        image = Image.new('RGBA', (64, 64))
        draw = ImageDraw.Draw(image)
        draw.rounded_rectangle((5, 8, 59, 45), radius=5, fill='#2879ca')
        draw.rectangle((10, 13, 54, 38), fill='#eaf4ff')
        draw.rectangle((28, 45, 36, 53), fill='#2879ca')
        draw.rounded_rectangle((18, 53, 46, 58), radius=2, fill='#2879ca')
        self.icon = pystray.Icon(
            'pc2mqtt', image, 'pc2mqtt — Connecting',
            menu=pystray.Menu(
                pystray.MenuItem(self._status, None, enabled=False),
                pystray.MenuItem('Open log', self._open_log),
                pystray.MenuItem('Quit', self._quit),
            ),
        )

    def _status(self, item=None):
        if self.failed:
            return 'Error — see log'
        return 'MQTT connected' if self.pc.client.is_connected() else 'MQTT disconnected — retrying'

    def _open_log(self, icon, item):
        try:
            os.startfile(str(self.log_path))
        except OSError:
            self.pc.logger.exception('Unable to open log')

    def _quit(self, icon, item):
        self.stopped.set()
        icon.stop()

    def _setup(self, icon):
        icon.visible = True
        self.worker.start()

    def _work(self):
        try:
            self.pc.client.loop_start()
            while not self.stopped.is_set():
                self.pc.poll()
                title = f'pc2mqtt — {self._status()}'
                if self.icon.title != title:
                    self.icon.title = title
                    self.icon.update_menu()
                self.stopped.wait(1)
        except Exception:
            self.failed = True
            self.pc.logger.exception('Background processing failed')
            self.icon.title = 'pc2mqtt — Error (see log)'
            self.icon.update_menu()
        finally:
            self.pc.close()

    def run(self):
        try:
            self.icon.run(setup=self._setup)
        finally:
            self.stopped.set()
            if self.worker.ident is not None:
                self.worker.join(timeout=10)
                if self.worker.is_alive():
                    self.pc.logger.warning('Background worker did not stop within ten seconds')
