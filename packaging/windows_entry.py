"""Launch the packaged application through the same entry point as desktop_app.py."""
import runpy


def main():
    runpy.run_module('desktop_app', run_name='__main__')
