#!/usr/bin/env python
"""Django's command-line utility for administrative tasks."""
import os
import sys


def main():
    """Run administrative tasks."""
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
    try:
        from django.core.management import execute_from_command_line
    except ImportError as exc:
        raise ImportError(
            "Couldn't import Django. Are you sure it's installed and "
            "available on your PYTHONPATH environment variable? Did you "
            "forget to activate a virtual environment?"
        ) from exc
    # Port par défaut de `runserver` configurable (RUNSERVER_PORT dans .env), utile si 8000 est déjà pris.
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), '.env'))
    if os.getenv('RUNSERVER_PORT'):
        from django.core.management.commands import runserver
        runserver.Command.default_port = os.environ['RUNSERVER_PORT']
    execute_from_command_line(sys.argv)


if __name__ == '__main__':
    main()
