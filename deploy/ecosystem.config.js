module.exports = {
  apps: [
    {
      name: "scpool",
      cwd: __dirname + "/..",
      script: "venv/bin/python",
      args: "-m uvicorn app.main:app --host 127.0.0.1 --port 8000",
      interpreter: "none",
      autorestart: true,
      watch: false,
      max_memory_restart: "500M",
      env: {
        PYTHONUNBUFFERED: "1",
        SCPOOL_LOCAL_HARDWARE: "0",
        SCPOOL_LOCAL_AUTOLOGIN: "0",
        SCPOOL_TRUSTED_PROXIES: "127.0.0.1"
      }
    }
  ]
};

