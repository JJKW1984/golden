import subprocess
import sys
import webbrowser
import time


def main():
    print("Running Alembic migrations...")
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=".",
        check=True,
    )
    print("Starting server on http://127.0.0.1:5000 ...")
    import threading

    def open_browser():
        time.sleep(1.5)
        webbrowser.open("http://127.0.0.1:5000")

    threading.Thread(target=open_browser, daemon=True).start()
    import uvicorn

    uvicorn.run(
        "finapp.main:app",
        host="127.0.0.1",  # loopback only — never 0.0.0.0
        port=5000,
        reload=False,
    )


if __name__ == "__main__":
    main()
