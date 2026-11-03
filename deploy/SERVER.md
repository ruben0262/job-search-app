# The server: a Proxmox VM, from nothing to the site answering

One virtual machine on the Proxmox host runs the whole platform as separate Docker containers, one per
part, so any part can be rebuilt or restarted while the rest keep running. The database is MongoDB
Atlas, off this machine, and the only way in is a Cloudflare Tunnel: the VM opens no port to the
internet. Day-to-day running is in [`README.md`](README.md).

## What it needs

Each container has a ceiling on memory and CPU in [`compose.yml`](compose.yml). A ceiling is a limit,
not a reservation; what each one actually uses was measured on 2026-11-03 where it matters:

| Container | Runs | Memory ceiling | Measured | CPU ceiling |
|---|---|---|---|---|
| `api` (site and API) | always | 1 GB | 324 MB: 4 workers, after 240 requests | 2 |
| `docgen` (CVs, with Chrome) | always | 1.5 GB | 560 MB at its peak over 10 CVs | 2 |
| `notifier` (alerts) | always | 512 MB | | 1 |
| `oauthbroker` (Claude sign-in) | always | 512 MB | | 1 |
| `cloudflared` (the tunnel) | always | 256 MB | | |
| `harvester` | every 15 minutes, for up to 14 | 1 GB | | 2 |
| `seed-ats` | when the company list changes | 512 MB | | 1 |

The always-on ceilings add up to 3.8 GB, 4.8 GB while a harvest runs. Building the images takes about
another 1.5 GB for a few minutes (the site's `npm` build, Chrome's packages).

**The VM: 4 vCPU, 8 GB RAM, 4 GB swap, 64 GB disk.** That leaves room for a build during a harvest
without swapping. It runs on 2 vCPU and 4 GB, but then a deploy during a harvest leans on the swap.
The disk holds about 5 GB of images, a build cache that grows until pruned, the logs, and `archive/`:
postings retention removes after 30 days, kept on disk for good (about 5 KB each).

**A VM, not an LXC container.** Docker inside LXC needs nesting, keyctl and, for overlay storage,
AppArmor exceptions, and it breaks in ways a VM does not. **One VM, not one per service.** The
containers already keep each part apart (its own image, its own limits, restarted alone), and
separate VMs would add a network between them, an image registry, and an operating system each to
keep up to date: more moving parts for the same result (CLAUDE.md §6).

## 1. The VM

In Proxmox, with the [Ubuntu Server 24.04 LTS](https://ubuntu.com/download/server) ISO uploaded to a
storage:

- **General:** a name, e.g. `mahoraga`. Tick *Start at boot*.
- **System:** machine `q35`, SCSI controller *VirtIO SCSI single*, tick *Qemu Agent*.
- **Disks:** 64 GB, bus *SCSI*, tick *Discard* and *IO thread*, and *SSD emulation* on SSD storage.
- **CPU:** 1 socket, 4 cores, type `host`.
- **Memory:** 8192 MB, *Ballooning* off: the containers' memory limits assume the memory is there.
- **Network:** bridge `vmbr0`, model *VirtIO*.

Install Ubuntu with the OpenSSH server, and an account for yourself (the examples say `ruben`). Give
the VM a fixed address on your network (a DHCP reservation in the router is simplest).

**`sudo`** is for the few things only root may do: installing packages and Docker, joining the
`docker` group, the swap, and installing the timers. Ubuntu Server has it. Debian leaves it out when
root is given a password at install, and Proxmox's container templates log in as root without it.
There, once, as root: `apt install -y sudo && usermod -aG sudo ruben`, then log in again as `ruben`.
`deploy.sh` and `compose.sh` never need it: they need the `docker` group, below.

Then, on it:

```bash
sudo apt update && sudo apt full-upgrade -y
sudo apt install -y qemu-guest-agent git python3-venv
sudo systemctl enable --now qemu-guest-agent
sudo timedatectl set-timezone Europe/Budapest      # yours: the nightly backup runs at 03:20 local

# 4 GB of swap (the installer's is often smaller)
sudo swapoff -a && sudo rm -f /swap.img
sudo fallocate -l 4G /swap.img && sudo chmod 600 /swap.img && sudo mkswap /swap.img && sudo swapon /swap.img
grep -q '^/swap.img' /etc/fstab || echo '/swap.img none swap sw 0 0' | sudo tee -a /etc/fstab
```

**Back the VM up from Proxmox:** Datacenter → Backup → Add, this VM, daily, mode *Snapshot*, to a
Proxmox Backup Server or a NAS. That keeps `certificates/server.env`, `archive/` and the nightly
database dumps, which all live on this disk. Atlas's free tier keeps no backups of its own, so the
nightly dump is the database's only one.

## 2. Docker

```bash
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker "$USER"            # then log out and back in, so `docker` works without sudo

# Keep each container's console log bounded: the default grows until the disk is full.
echo '{"log-driver": "json-file", "log-opts": {"max-size": "10m", "max-file": "3"}}' \
  | sudo tee /etc/docker/daemon.json
sudo systemctl restart docker
docker compose version
```

## 3. The code

The repository is private, so the VM reads it with a deploy key: read-only, and for this repository
only.

```bash
ssh-keygen -t ed25519 -N "" -C "mahoraga-server" -f ~/.ssh/github_deploy
cat ~/.ssh/github_deploy.pub
```

GitHub → `ruben0262/job-search-app` → Settings → Deploy keys → *Add deploy key*: paste it, and leave
*Allow write access* off. Then:

```bash
printf 'Host github.com\n  IdentityFile ~/.ssh/github_deploy\n  IdentitiesOnly yes\n' >> ~/.ssh/config
git clone git@github.com:ruben0262/job-search-app.git ~/job-search-app
cd ~/job-search-app

# For the scripts that run on the VM itself: the health check, the backup, the account tools.
python3 -m venv .venv && .venv/bin/pip install -r services/api/requirements.txt
```

That `.venv` needs **Python 3.12 or newer**, as the code is written for it: `python3 --version`.
Ubuntu 24.04's is 3.12. Debian 12's is 3.11, and there `uv` fetches a 3.12 instead:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh && . ~/.local/bin/env
uv venv --python 3.12 .venv && uv pip install --python .venv/bin/python -r services/api/requirements.txt
```

## 4. The settings: `certificates/server.env`

The server's settings and secrets live in `certificates/`, as your laptop's `dev.env` does: never in
`deploy/`, and never in git. Everything on the server reads this one file: `deploy/compose.sh`, which
every compose command goes through, and the health and backup timers.

```bash
mkdir -p certificates && chmod 700 certificates
cp deploy/server.env.example certificates/server.env && chmod 600 certificates/server.env
nano certificates/server.env
```

Each setting says in the file what it is and how to make it. In short:

| Setting | What to put |
|---|---|
| `MONGODB_URI` | The Atlas address from your laptop's `certificates/mongodb.env`, in single quotes |
| `MONGODB_DB` | `mahoraga` |
| `SECRETS_KEK` | **The old server's value**, if you are bringing its accounts (step 9): their saved keys open with it and nothing else. Otherwise a new one: `.venv/bin/python -c "from libs.secrets import generate_key; print('k0:' + generate_key())"`. Keep a copy in your password manager too |
| `ACCESS_TOKEN_SECRET` | `python3 -c "import secrets; print(secrets.token_urlsafe(48))"` |
| `PUBLIC_URL` | `https://mahoraga.rubenlekli.com`, the address people open the site at: every sign-up, reset and alert link starts with it. The deploy checks it reaches this server (`deploy/reach.sh`) |
| `ADMIN_EMAILS` | `lekliruben@gmail.com` |
| `TUNNEL_TOKEN` | Step 6 |
| `SMTP_PASSWORD` | The Zoho mailbox's password (an app password, with 2FA) |
| `VAPID_*` | Made **once**, with the command in the file (run it with `.venv/bin/python`). A new pair stops every browser that already subscribed |
| `HARVEST_PROXY` | If the Proxmox host is on the same network as your proxy (`192.168.50.223`), the same value as on your laptop: LinkedIn then works from here. Otherwise empty, and LinkedIn is skipped |
| the rest | Optional: the platform's AI key, and the Adzuna, Jooble and Findwork keys |

Check it without starting anything: `deploy/compose.sh config --quiet && echo ok` names any required
setting that is missing.

## 5. Atlas

Atlas → Network Access → *Add IP Address*: this server's public address (`curl -s https://ifconfig.me`
on the VM). **A home connection's address can change**, and the day it does the site loses its
database. Ask your provider for a fixed one, or update the entry when it changes. To check from the VM:

```bash
set -a && . certificates/server.env && set +a
.venv/bin/python -c "from libs.db import database; print(database().command('ping'))"   # {'ok': 1.0}
```

## 6. The tunnel, and its token

**Make a new tunnel for this server rather than reusing the old one's token.** Two machines running the
same token are two connectors of one tunnel, and Cloudflare sends each visitor to either of them: half
would reach the old server.

1. Cloudflare → Zero Trust → Networks → Tunnels → *Create a tunnel* → *Cloudflared*, named e.g.
   `mahoraga-proxmox`.
2. Under *Install and run a connector*, choose *Docker*, and copy the long string after `--token`. Put
   it in `certificates/server.env` as `TUNNEL_TOKEN='…'`. Nothing else is needed on the VM: the
   `cloudflared` container is part of the stack.
3. *Public Hostname* → add a **test name** first, e.g. `mahoraga-new.rubenlekli.com`: type **HTTP**,
   URL **`platform-api:8000`**.
   - That address is the API's name on the containers' own network. Not `localhost`: the tunnel runs
     in a container beside the API, and the API opens no port on the VM.
   - Not `api:8000` either: `compose.yml` explains the name clash it avoids.
   `PUBLIC_URL` must be a name this tunnel serves, or the emails' links land nowhere: while you test,
   the test name; after step 4, the real one. Every deploy checks it (`deploy/reach.sh`).
4. Once the test name works (step 7), move the real name, `mahoraga.rubenlekli.com`:
   - in the **old** tunnel's Public Hostnames, delete it;
   - in the **new** one, add it with the same `HTTP` and `platform-api:8000`. If Cloudflare says a DNS
     record already exists, delete that `CNAME` under DNS first.
   - Set `PUBLIC_URL=https://mahoraga.rubenlekli.com`, run `deploy/deploy.sh api notifier`, then stop
     the old server's stack and delete the test name.

If a token ever leaks: the tunnel's page → *Refresh token*, put the new one in `server.env`, then
`deploy/compose.sh up -d cloudflared` (it is recreated with the new token).

## 7. Start it

```bash
cd ~/job-search-app
deploy/deploy.sh                 # the first build takes 10–15 minutes
sudo deploy/install-units.sh     # the harvest, health and backup timers
```

`deploy.sh` does everything in order and stops at the first thing that fails:
- builds every image;
- applies the database's schema on Atlas;
- starts each service;
- brings up the tunnel;
- checks each container is running this commit's code;
- starts the company-board seed in the background, the first time (about 15 minutes; its report
  is `logs/seed_ats.log`).

```bash
deploy/compose.sh ps                                     # api, docgen, notifier, oauthbroker, cloudflared: running
curl -s https://mahoraga-new.rubenlekli.com/health       # {"status":"ok"}
systemctl list-timers harvester.timer health.timer backup.timer
```

Then sign up on the site with the `ADMIN_EMAILS` address: Admin appears in the bar.

**`deploy.sh` starts the notifier, which messages real people.** With accounts brought from the old
server (step 9), their alerts begin at once. To start without them, deploy the rest first, then the
notifier when you mean to:

```bash
deploy/deploy.sh api docgen oauthbroker harvester seed-ats
deploy/deploy.sh notifier
```

## 8. Changing one part without taking the site down

```bash
deploy/deploy.sh docgen        # pulls, rebuilds and restarts docgen alone; the site keeps answering
deploy/deploy.sh notifier api  # any set of them
deploy/compose.sh stop notifier && deploy/compose.sh up -d notifier   # pause and resume alerts
deploy/compose.sh logs -f --tail 100 docgen                         # what one part is saying
```

Restarting one service leaves every other one running. The exception is `api`: it is the site, and
there is one of it, so the site is away for the few seconds the new container takes to start.
Harvesting, alerts and CVs being made carry on meanwhile, and a CV asked for waits in its queue.

To undo a bad change: revert the commit on your laptop, push, then `deploy/deploy.sh <the service>`.

## 9. The accounts on the old server (once, optional)

The old VPS still holds the accounts, in Postgres. [`README.md`](README.md), *Moving the accounts off
the old VPS*, copies them into Atlas. Do it before step 7, and use the old server's `SECRETS_KEK`
here, or the copied credentials cannot be opened.
