# Deployment Guide — mpr.gokwik.co

Amazon Linux 2023 · Nginx · Python 3 · systemd

---

## 1. EC2 Security Group

Open these inbound ports before anything else:

| Port | Protocol | Source | Purpose |
|------|----------|--------|---------|
| 22   | TCP | Your IP only | SSH |
| 80   | TCP | 0.0.0.0/0 | HTTP (Nginx) |
| 443  | TCP | 0.0.0.0/0 | HTTPS (after SSL) |

Port 8080 does **not** need to be open — Nginx proxies to it internally.

---

## 2. SSH into the instance

```bash
ssh -i your-key.pem ec2-user@<EC2-PUBLIC-IP>
```

---

## 3. Install dependencies

```bash
sudo dnf update -y
sudo dnf install -y git nginx python3

# Verify
python3 --version
nginx -v
```

---

## 4. Clone the repo

```bash
cd /opt
sudo git clone https://github.com/shreyaskarsingh-sudo/Gk-product-radar.git mpr
sudo chown -R ec2-user:ec2-user /opt/mpr
cd /opt/mpr
```

---

## 5. Configure environment

```bash
cp .env.example .env
nano .env
```

Fill in your values:

```
METABASE_URL=https://internal-stats.gokwik.in
METABASE_API_KEY=mb_your_metabase_api_key_here
ANTHROPIC_API_KEY=sk-ant-your_anthropic_key_here
PORT=8080
```

Then generate `config.js`:

```bash
bash setup.sh
```

Verify it was created:

```bash
cat config.js   # should show your keys
```

---

## 6. Create systemd service

This keeps the Python server running across reboots and auto-restarts on crash.

```bash
sudo nano /etc/systemd/system/mpr.service
```

Paste this exactly:

```ini
[Unit]
Description=GoKwik Merchant Product Radar
After=network.target

[Service]
Type=simple
User=ec2-user
WorkingDirectory=/opt/mpr
ExecStart=/usr/bin/python3 /opt/mpr/server.py
Restart=always
RestartSec=5
EnvironmentFile=/opt/mpr/.env

[Install]
WantedBy=multi-user.target
```

Enable and start:

```bash
sudo systemctl daemon-reload
sudo systemctl enable mpr
sudo systemctl start mpr
sudo systemctl status mpr    # should show "active (running)"
```

Test locally on the EC2:

```bash
curl -s -o /dev/null -w "%{http_code}" http://localhost:8080/merchant-product-radar.html
# should print 200
```

---

## 7. Nginx config

```bash
sudo nano /etc/nginx/conf.d/mpr.conf
```

Paste:

```nginx
server {
    listen 80;
    server_name mpr.gokwik.co;

    # Security headers
    add_header X-Frame-Options "SAMEORIGIN";
    add_header X-Content-Type-Options "nosniff";
    add_header Referrer-Policy "strict-origin-when-cross-origin";

    # Proxy everything to the Python server
    location / {
        proxy_pass         http://127.0.0.1:8080;
        proxy_http_version 1.1;
        proxy_set_header   Host              $host;
        proxy_set_header   X-Real-IP         $remote_addr;
        proxy_set_header   X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header   X-Forwarded-Proto $scheme;
        proxy_read_timeout 60s;
    }
}
```

Test and reload:

```bash
sudo nginx -t                  # must say "syntax is ok"
sudo systemctl enable nginx
sudo systemctl start nginx
sudo systemctl reload nginx
```

---

## 8. DNS record

Ask your DevOps team to add this A record in the gokwik.co DNS zone:

```
Type:  A
Name:  mpr
Value: <EC2-PUBLIC-IP>
TTL:   300
```

Once propagated, `http://mpr.gokwik.co` will serve the tool.

Verify propagation:

```bash
nslookup mpr.gokwik.co
# or
dig mpr.gokwik.co +short
```

---

## 9. SSL certificate (later — manual DNS challenge)

When your DevOps team is ready:

```bash
sudo dnf install -y certbot python3-certbot-nginx

sudo certbot certonly \
  --manual \
  --preferred-challenges dns \
  -d mpr.gokwik.co
```

Certbot will print a TXT record like:

```
_acme-challenge.mpr.gokwik.co  →  <random-token>
```

Give that to DevOps to add in DNS, wait ~2 min, then press Enter to complete.

After the cert is issued, update the Nginx config:

```bash
sudo nano /etc/nginx/conf.d/mpr.conf
```

Replace the entire file with:

```nginx
server {
    listen 80;
    server_name mpr.gokwik.co;
    return 301 https://$host$request_uri;
}

server {
    listen 443 ssl;
    server_name mpr.gokwik.co;

    ssl_certificate     /etc/letsencrypt/live/mpr.gokwik.co/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/mpr.gokwik.co/privkey.pem;
    ssl_protocols       TLSv1.2 TLSv1.3;
    ssl_ciphers         HIGH:!aNULL:!MD5;

    add_header Strict-Transport-Security "max-age=31536000" always;
    add_header X-Frame-Options "SAMEORIGIN";
    add_header X-Content-Type-Options "nosniff";

    location / {
        proxy_pass         http://127.0.0.1:8080;
        proxy_http_version 1.1;
        proxy_set_header   Host              $host;
        proxy_set_header   X-Real-IP         $remote_addr;
        proxy_set_header   X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header   X-Forwarded-Proto $scheme;
        proxy_read_timeout 60s;
    }
}
```

Then:

```bash
sudo nginx -t && sudo systemctl reload nginx
```

---

## Useful commands after deploy

```bash
# View live app logs
sudo journalctl -u mpr -f

# Restart app (after pulling updates)
sudo systemctl restart mpr

# Pull latest code and redeploy
cd /opt/mpr
git pull
bash setup.sh          # regenerate config.js if .env changed
sudo systemctl restart mpr

# Check Nginx logs
sudo tail -f /var/log/nginx/access.log
sudo tail -f /var/log/nginx/error.log
```
