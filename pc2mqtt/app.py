"""Command-line entry point and Windows tray startup."""

import argparse
import sys

from pc2mqtt import PC2MQTT


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('--host', required=True)
    parser.add_argument('--port', default=1883, type=int)
    parser.add_argument('--keepalive', default=60, type=int)
    parser.add_argument(
        '--tray', action='store_true',
        default=sys.platform == 'win32' and getattr(sys, 'frozen', False),
        help='Run in the Windows system tray (default for the Windows executable)',
    )
    args = parser.parse_args(argv)
    if args.tray and sys.platform != 'win32':
        parser.error('--tray is only supported on Windows')
    if args.tray:
        from pc2mqtt.tray import configure_logging, WindowsTray

        log_path = configure_logging()
        try:
            pc = PC2MQTT(args.host, args.port, args.keepalive, connect_async=True)
            WindowsTray(pc, log_path).run()
        except Exception:
            import logging
            logging.getLogger('pc2mqtt').exception('Tray application failed')
            raise
    else:
        pc = PC2MQTT(args.host, args.port, args.keepalive)
        pc.client.loop_start()
        try:
            pc.state()
        finally:
            pc.close()


if __name__ == '__main__':
    main()
