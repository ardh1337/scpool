module.exports = {
  apps: [
    {
      name: "scpool",
      cwd: "/root/scpool",
      script: "/root/scpool/venv/bin/python",
      args: "-m uvicorn app.main:app --host 127.0.0.1 --port 8000",
      interpreter: "none",
      autorestart: true,
      watch: false,
      max_memory_restart: "500M",
      env: {
        PYTHONUNBUFFERED: "1"
      }
    }
  ]
};
