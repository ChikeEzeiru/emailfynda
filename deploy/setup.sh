#!/bin/bash
# Run this once on a fresh Ubuntu 22.04 Oracle Cloud VM.
# Usage: bash setup.sh YOUR_HUNTER_API_KEY

set -e

HUNTER_API_KEY="${1:-}"
APP_DIR="/home/ubuntu/emailfynda"

echo "==> Updating packages..."
sudo apt-get update -y && sudo apt-get upgrade -y

echo "==> Installing dependencies..."
sudo apt-get install -y python3 python3-pip python3-venv git nginx

echo "==> Cloning repo..."
git clone https://github.com/ChikeEzeiru/emailfynda.git "$APP_DIR"

echo "==> Setting up Python venv..."
cd "$APP_DIR/backend"
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

echo "==> Creating systemd service..."
sudo tee /etc/systemd/system/emailfynda.service > /dev/null <<EOF
[Unit]
Description=EmailFynda Flask API
After=network.target

[Service]
User=ubuntu
WorkingDirectory=$APP_DIR/backend
Environment=PATH=$APP_DIR/backend/venv/bin
Environment=HUNTER_API_KEY=$HUNTER_API_KEY
ExecStart=$APP_DIR/backend/venv/bin/gunicorn -w 2 -b 127.0.0.1:5050 server:app
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

echo "==> Creating nginx config..."
sudo tee /etc/nginx/sites-available/emailfynda > /dev/null <<'EOF'
server {
    listen 80;
    server_name _;

    location / {
        proxy_pass http://127.0.0.1:5050;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_read_timeout 60s;
    }
}
EOF

sudo ln -sf /etc/nginx/sites-available/emailfynda /etc/nginx/sites-enabled/emailfynda
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t
sudo systemctl restart nginx

echo "==> Starting emailfynda service..."
sudo systemctl daemon-reload
sudo systemctl enable emailfynda
sudo systemctl start emailfynda

echo ""
echo "==> Done! Check status with: sudo systemctl status emailfynda"
echo "==> Test with: curl http://localhost/"
