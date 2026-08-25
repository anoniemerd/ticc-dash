# TICC-DASH

<p align="center">
  <img width="500" height="500" alt="ticc-dash-logo-dark-without-background" src="https://github.com/user-attachments/assets/35b6d438-60da-485b-a0d4-c01708e4b059" />
</p>

<h1 align="center">TICC-DASH</h1>
<p align="center"><em>Time Information of Chrony Clients - Dashboard</em></p>

<p align="center">
  <a href="https://ticc-dash.org" target="_blank"><img src="https://img.shields.io/badge/website-ticc--dash.org-0ea5a5?style=flat-square"/></a>
  <img src="https://img.shields.io/badge/version-3.0-blue?style=flat-square"/>
  <img src="https://img.shields.io/badge/python-3.10%2B-green?style=flat-square"/>
  <img src="https://img.shields.io/badge/license-MIT-yellow?style=flat-square"/>
</p>

A sleek, live‑updating web interface to monitor your **Chrony NTP clients**. Built with Python (Flask) · Bootstrap 5 · Vanilla JS + AJAX (jQuery) · Chrony/chronyc · systemd

**Formerly known as Chrony NTP Web Interface (V2) - now improved and rebranded as TICC-DASH.** 
---

## 🔗 Quick links

- 🌐 **Website:** <https://ticc-dash.org>
- 📥 **Install guide:** <https://ticc-dash.org/install.html>
- 🗑️ **Uninstall guide:** <https://ticc-dash.org/uninstall.html>
- 📚 **Docs (how it works, service commands):** <https://ticc-dash.org/docs.html>
- 🖼️ **Screenshots:** <https://ticc-dash.org/screenshots.html>
- ℹ️ **About & background:** <https://ticc-dash.org/about.html>

---

## ✨ What’s new vs. the old version

- 🎯 **New brand & visuals** - fresh logo, modern typography & improved layout.
- 🧭 **Centered header** - logo and title perfectly aligned and responsive.
- 🟢 **Improved status indicators** - compact OK / Warning / Critical badges.
- 🌓 **Dynamic light/dark themes** with theme persistence.
- 🔎 **Real‑time search, sorting, and client statistics**.
- 🔄 **Expandable client rows** for detailed metrics.
- 💾 **Local storage** remembers your theme and expanded rows.
- 🧩 **More robust `chronyc` parsing** for hostnames, IPv4, and IPv6.
- 🧱 **Production‑grade installation** using `systemd` & `bash`.
- 📦 **Logical system path:** `/opt/ticc-dash` instead of a user’s home folder.
- ⚙️ **Automatic systemd setup** with start‑on‑boot and journald logging.
- 🚀 **One‑line install & uninstall scripts**.

---


## 📷 Screenshots

<img width="1910" height="976" alt="ticc-dash1" src="https://github.com/user-attachments/assets/aa6b70ad-9c64-4914-8f49-020a40c583ef" />

<img width="1912" height="975" alt="ticc-dash4" src="https://github.com/user-attachments/assets/8b25e76c-b0f0-458c-95c8-83d77eda3639" />


> For more screenshots, see the screenshots page: <https://ticc-dash.org/screenshots.html>.

---

## 🚀 Quick Install

Installs into `/opt/ticc-dash` and runs automatically as a system service:

```bash
curl -fsSL https://raw.githubusercontent.com/anoniemerd/ticc-dash/main/install_ticc_dash.sh | bash
```

Then open the dashboard at:

```
http://<your-server-ip>:5000/
```

> For more information see <https://ticc-dash.org/install.html>.

### 🧹 Uninstall

Clean removal (service, files, and sudoers entry):

```bash
curl -fsSL https://raw.githubusercontent.com/anoniemerd/ticc-dash/main/uninstall_ticc_dash.sh | bash
```

Full uninstall notes: <https://ticc-dash.org/uninstall.html>.

---

## 🧠 How it works

- Runs `chronyc clients` via `sudo` to collect live NTP client data.
- Parses and groups hostnames, IPv4 and IPv6.
- Exposes two endpoints: `/` (dashboard UI) and `/data` (JSON).
- Frontend uses small AJAX calls every second for live updates.
- Sorting, filtering, and row expansion handled client‑side.

Technical deep‑dive: <https://ticc-dash.org/docs.html>.

---

## ⚙️ Requirements

- Debian/Ubuntu Linux
- Python **3.10+**
- **chrony** service installed and active
- Sudo access for `/usr/bin/chronyc` (the installer configures a sudoers rule)

---

## 🧩 Installed structure

```
/opt/ticc-dash
├── ticc-dash.py
├── venv/
└── static/
    └── img/
        └── ticc-dash-logo.png
```

Systemd unit: `/etc/systemd/system/ticc-dash.service`  
Sudoers rule: `/etc/sudoers.d/ticc-dash`

---

## 🔧 Managing the service

```bash
sudo systemctl status ticc-dash.service
sudo systemctl restart ticc-dash.service
sudo journalctl -u ticc-dash.service -f
```

More commands & explanations: <https://ticc-dash.org/docs.html#manage-the-systemd-service>.

---

## 🔁 Upgrading from the old version (Chrony NTP Web Interface V2)

If you used **Chrony NTP Web Interface V2**, migrate easily:

1. Stop and remove the old service
```bash
sudo systemctl stop chronyweb.service
sudo systemctl disable chronyweb.service
sudo rm /etc/systemd/system/chronyweb.service
sudo rm -rf ~/chrony_web
```

2. Run the TICC‑DASH installer
```bash
curl -fsSL https://raw.githubusercontent.com/anoniemerd/ticc-dash/main/install_ticc_dash.sh | bash
```

3. Then open the dashboard at:
```
http://<your-server-ip>:5000/
```

---

## Configuration

The application can be configured using environment variables.

| Variable | Default | Description |
|---|---|---|
| `TZ` | `Etc/UTC` | Timezone used by the application. |
| `CHRONY_USE_SUDO` | `true` | Whether to execute `chronyc` through `sudo`. Set to `false` when the container user can access the Chrony command socket directly. |
| `CHRONY_SOCKET` | unset | Path to the Chrony Unix command socket. When set, `chronyc` is invoked with `-n -h <socket>`. |

For example:

```bash
TZ: "Europe/Warsaw"
CHRONY_USE_SUDO: "false"
CHRONY_SOCKET: "/var/run/chrony/chronyd.sock"
```
With this configuration, the application executes:

`chronyc -n -h /var/run/chrony/chronyd.sock clients`

## Containers

The application can be run as a container using the provided `Dockerfile`:

```bash
docker build -t ticc-dash .
docker run --rm -p 5000:5000 ticc-dash
```

When running alongside a Chrony container, mount the Chrony runtime socket into the dashboard container:

`docker run --rm -v /var/run/chrony:/var/run/chrony:ro -p 5000:5000 ticc-dash`

The container by default listens on port `5000`.

## 🧠 Troubleshooting

| Problem | Solution |
|--------|----------|
| ❌ No clients showing | Run `sudo chronyc clients` manually to verify |
| ⚙️ Service not starting | Check logs: `sudo journalctl -u ticc-dash.service -f` |
| 🔒 Port already in use | Free port 5000 or put a reverse proxy (e.g., Nginx) in front |
| 🧩 Missing logo | Ensure `ticc-dash-logo.png` exists under `/opt/ticc-dash/static/img/` |

More tips: <https://ticc-dash.org/docs.html#troubleshooting>.

---

## 👤 Author & License

- Author: **Anoniemerd** — <https://github.com/anoniemerd>
- Website: <https://ticc-dash.org>
- Container support: **Venthe** - <https://github.com/venthe>

Released under the **MIT License**.  
© 2025 – TICC‑DASH Project.
