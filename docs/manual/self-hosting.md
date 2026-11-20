# Running Zone yourself

This page is for whoever installs and runs a Zone: on a laptop, a home server or a NAS.
It covers installing Zone, where its settings live, setting a login, reaching it from other devices, and what to do before you open it to the internet.
The full details are in [the deployment spec](../spec/deployment.md) and [the security spec](../spec/security.md).

## Download the binary

The quickest way to run Zone on your own computer, on Linux or a Mac: one file, nothing to build.
Zone needs git, for the wiki's history.
On a Mac, `xcode-select --install` installs it (or `brew install git`); on Linux, your package manager does (`sudo apt install git`).

1. Download the file for your computer from the [latest release](https://github.com/zonewiki/zonewiki/releases/latest):

   | Your computer | The file |
   |---------------|----------|
   | A Mac with Apple silicon (M1 or later) | `zonewiki-macos-arm64` |
   | A Mac with an Intel processor | `zonewiki-macos-amd64` |
   | Linux on a PC | `zonewiki-linux-amd64` |
   | Linux on ARM, such as a Raspberry Pi 4 or 5 (64-bit) | `zonewiki-linux-arm64` |

2. In a terminal, name it `zonewiki`, make it a program, and start it:

   ```bash
   cd ~/Downloads
   mv zonewiki-macos-arm64 zonewiki      # the file you downloaded
   chmod +x zonewiki
   ./zonewiki
   ```

3. Open <http://localhost:5000>.

Your wiki is the folder `zonewiki` in your home folder, made at the first start; the terminal says where.
To keep it elsewhere, or to use a folder of Markdown notes you already have, start Zone with that folder: `ZONE_DATA=~/Documents/notes ./zonewiki`.
Zone runs as long as the terminal is open; `Ctrl+C` stops it, and your wiki stays as it is.

On a Mac, the binary isn't signed, so macOS won't open a downloaded one ("Apple could not verify…").
Take the download mark off it once, then start it again:

```bash
xattr -d com.apple.quarantine zonewiki
```

If port 5000 is taken, Zone says so and stops; on a Mac, AirPlay Receiver often has it.
Start Zone on another port, `./zonewiki --addr :5001`, and open <http://localhost:5001>.

The commands this page shows as `./run.sh …` are `./zonewiki …` here (`./zonewiki config passwd`, `./zonewiki user add anna`), and the `.env` settings are environment variables you set when you start it (`ZONE_ALLOW_REMOTE=1 ./zonewiki`).
`./zonewiki --help` lists them all.
The binary searches by keywords, not by meaning; how it is built is in [Release binaries](../spec/deployment.md#release-binaries).

## With Docker

You need Docker with Compose, or Podman with `podman-compose`.
Zone is tested on Linux, and should work on macOS and in WSL2 on Windows.

```bash
git clone https://github.com/zonewiki/zonewiki && cd zonewiki
mkdir -p ~/zonewiki                   # your wiki folder
cp .env.example .env                  # ZONE_DATA is your wiki folder
./run.sh up                           # build and start
```

Open <http://localhost:5000>.
The first build takes a few minutes; later starts are quick.

The wiki folder must exist before Zone starts: Zone won't create one where a typo points.
To use a folder of Markdown notes you already have, set `ZONE_DATA` in `.env` to it.
Zone makes it a git repository if it isn't one, and adds a home page.
What ends up in the folder is described in [Your data](your-data.md).

`./run.sh` works out what compose can't: your user ID (so the wiki's files stay yours), your timezone, and whether to listen beyond localhost.
To run compose without it, see [Without run.sh](../spec/deployment.md#without-runsh).

## On a NAS or server

A NAS, a Raspberry Pi or a small server with Docker and Compose can run the prebuilt image, `ghcr.io/zonewiki/zonewiki` (for amd64 and arm64), without the repository or a build.

1. Create the wiki folder, empty or with Markdown files in it.
2. Put `compose.nas.yaml` from the repository in a folder, and next to it a `.env` with:
   - `ZONE_DATA`: the wiki folder, as an absolute path (`/volume1/docker/zonewiki/wiki`);
   - `ZONE_UID` and `ZONE_GID`: the IDs of the folder's owner (`id -u` and `id -g` as that user);
   - `TZ`, such as `Europe/Vienna`, so "today" is your day;
   - optionally `ZONE_PORT`, the port on the host (default 5080).
3. Start it: `docker compose -f compose.nas.yaml up -d`.

Zone is then at `http://<nas>:5080`.
The commands that `./run.sh` gives you run through the container instead:

```bash
docker compose -f compose.nas.yaml run --rm zonewiki /app/zonewiki config set hosts nas.local, 192.168.1.20
docker compose -f compose.nas.yaml run --rm zonewiki /app/zonewiki user add anna
```

For a Synology with Container Manager, step by step, see [Synology](../spec/deployment.md#synology).

## Without a container

With Go 1.27 or newer, a C compiler and git:

```bash
(cd go && go build -o zonewiki .)
go/zonewiki
```

The wiki is then the folder `zonewiki` in your home folder, made at the first start; set `ZONE_DATA` to use another (`ZONE_DATA=/path/to/wiki go/zonewiki`).
The commands are `zonewiki user …`, `zonewiki group …` and `zonewiki config …`, on the same wiki.
`zonewiki --help` lists them all.
Run by hand, Zone listens on every interface, so set a login before other machines can reach it.
See [Without a container](../spec/deployment.md#without-a-container), and for a Unix socket or systemd, [A Unix socket](../spec/deployment.md#a-unix-socket).

## Where settings live

Settings live in two places:

- **`.env`**, beside `run.sh` or the compose file: how Zone runs (with the downloaded binary, environment variables you start it with).
  The wiki folder, the timezone, network access, mail, a public wiki.
  A change takes effect at the next `./run.sh up`.
- **`.zone/config`**, in the wiki folder: the wiki itself.
  Its users and their roles, the login, the names it's reached by, limits and everything else.
  Zone reloads it within a second of a change, so no restart is needed.

Change `.zone/config` with `./run.sh config set KEY VALUE` (and `config unset KEY` for the default), or edit it by hand.
A setting Zone doesn't know, or a value that doesn't check, is an error that names its line.
A running Zone then keeps the settings it had and says so in its log; a Zone starting up refuses to start until it is fixed.
`./run.sh config check` shows each setting in effect and where it comes from.

### The settings people change most

| Setting | Where | What it does |
|---------|-------|--------------|
| `ZONE_DATA` | `.env` | The wiki folder (default `~/zonewiki`) |
| `TZ` | `.env` | The timezone, such as `Europe/Berlin` (default: the host's) |
| `ZONE_ALLOW_REMOTE` | `.env` | `1`: other devices on the network reach Zone |
| `ZONE_PUBLIC` | `.env` | `view` or `edit`: people read, or edit, without signing in |
| `ZONE_SIGNUP`, `ZONE_SMTP_*` | `.env` | Sign-up and the mail server ([Mail](#mail)) |
| `ZONE_PORT` | `.env` | The host's port, with `compose.nas.yaml` (default 5080) |
| `hosts` | `.zone/config` | Names Zone is opened by: `wiki.example.org, nas.local` |
| `base-url` | `.zone/config` | Zone's address as people know it: `https://wiki.example.org` |
| `git-remote` | `.zone/config` | A remote to push the history to ([Your data](your-data.md#backups)) |
| `locale` | `.zone/config` | How dates read and when weeks start: `de-AT` |
| `date-format` | `.zone/config` | `iso`: dates as `2026-11-19` |
| `upload-max` | `.zone/config` | The biggest upload (default `100MB`) |
| `storage-max` | `.zone/config` | How much the wiki may hold (default: no limit) |
| `external-content` | `.zone/config` | Sites pages may show images and frames from: `upload.wikimedia.org` |
| `trusted-proxies` | `.zone/config` | A reverse proxy's address, when it isn't on the same machine |

Every `.env` setting is in `.env.example` with a comment.
Every `.zone/config` key is in [The wiki's keys](../spec/users.md#the-wikis-keys).

## Setting a login

On localhost, out of the box, Zone has no login: whoever can reach the port can do everything.
That's fine on your own laptop, and nowhere else.

As soon as other devices can reach Zone, it shows nothing until a login is set.
Open it in a browser, and it asks for a password (at least 8 characters, twice), then **Set the password**:

- leave **User name (optional)** empty, and the wiki gets one password;
- fill it in, and you become the wiki's first user and its admin, and can invite others.

Whoever reaches a new wiki first sets its login, so do this right after starting it.
If you meet a login you didn't set, remove it on the host, in `.zone/config`.

From the terminal it's the same:

```bash
./run.sh config passwd          # one password for the wiki
./run.sh user add anna          # or: a first user, who is an admin
```

Adding users, roles, groups and invites is in [Teams](teams.md).
To keep a wiki on the network open on purpose, without a login: `./run.sh config set no-login yes`.

## Reaching it from other devices

By default Zone listens only on the machine it runs on.
To reach it from your phone or another computer on the same network:

1. Set `ZONE_ALLOW_REMOTE=1` in `.env`.
2. Run `./run.sh up` again.
3. Open `http://<the machine's address>:5000`, such as `http://192.168.1.20:5000`.

Any IP address then works.
To open Zone by a name instead (`nas.local`, `wiki.example.org`), add the name to `hosts`:

```bash
./run.sh config set hosts nas.local, wiki.example.org
```

A browser that opens Zone by a name not in `hosts` gets an error page saying so, and the log names it.
This check stops other websites from reaching your wiki through your browser.

On a phone, Zone can be added to the home screen as an app.
Android installs it as a real app only over HTTPS.

## Before you open it up

Plain `http://` is fine on localhost.
Anywhere else, read this first.

- **Use HTTPS.** Over plain `http://` your password, your login and your pages travel unencrypted: anyone on the same Wi-Fi can read them and sign in as you.
- **Set a login**, with a long, unique password. There is no second factor.
- **For just yourself, prefer a VPN** (WireGuard, Tailscale) to a public address.
- **Know who can read what.** Every user reads everything except other users' private pages and the pages of groups they aren't in. Private pages are hidden inside Zone, not encrypted: whoever has the folder or its git history reads them all.
- **Make admin only people you trust with everyone's pages.**
- **Keep tokens secret.** An assistant's token can change and delete pages as its user can. You can connect an assistant that only reads ([AI assistants](assistants.md)).
- **Treat the wiki folder, its git remote and every backup as confidential.**

The full list is in [Before you open it up](../spec/security.md#before-you-open-it-up).

### HTTPS with a reverse proxy

Put a reverse proxy in front of Zone that handles HTTPS.
[Caddy](https://caddyserver.com) on the same machine gets the certificate by itself:

```
wiki.example.org {
    reverse_proxy localhost:5000
}
```

Then tell Zone its name and address, and set the login:

```bash
./run.sh config set hosts wiki.example.org
./run.sh config set base-url https://wiki.example.org
./run.sh config passwd
```

Leave `ZONE_ALLOW_REMOTE` off: the proxy reaches Zone on localhost, and nobody can go around HTTPS to port 5000.
A proxy on another machine needs `ZONE_ALLOW_REMOTE=1`, and its address in `trusted-proxies` so that wrong passwords are counted per visitor, not all as the proxy's.
Any other proxy must pass on the original host name and say the request came over HTTPS (`X-Forwarded-Proto`).
See [Self-hosting behind HTTPS](../spec/security.md#self-hosting-behind-https).

## Mail

Zone sends mail for two things: sign-up, and telling users about their tasks (a task given to them, a mention, a comment), for those who turned it on in My tasks' settings.
It needs an SMTP server, set in `.env`:

```bash
ZONE_SMTP_HOST=smtp.example.org
ZONE_SMTP_PORT=587
ZONE_SMTP_USER=wiki@example.org
ZONE_SMTP_PASSWORD=…
ZONE_SMTP_TLS=starttls             # starttls (default), tls (port 465) or none
ZONE_MAIL_FROM=wiki@example.org
```

Mail is always encrypted unless you set `ZONE_SMTP_TLS=none`, for a relay on the same machine.
Zone won't start on a mail setting that doesn't make sense, and says why.

A user gets mail only once they have an address: sign-up records it, or an admin sets it with `./run.sh user email anna anna@example.org`.

### Sign-up

`ZONE_SIGNUP=1` lets people ask for an account with their mail address.
It also needs users and `base-url` in `.zone/config`.
There, `signup-domains` limits it to addresses at your domains, `signup-role` gives new accounts `viewer` (the default) or `editor`, and `signup-approval = yes` has an admin approve each one.
See [Sign-up](../spec/users.md#sign-up).

## A public wiki

`ZONE_PUBLIC=view` in `.env` lets anyone read the wiki's shared pages without signing in, and `ZONE_PUBLIC=edit` lets them edit too; see [Teams](teams.md).

## Updating Zone

Your wiki is never touched by an update: it lives in its own folder.

| Installed with | To update |
|----------------|-----------|
| The repository and `./run.sh` | `git pull && ./run.sh up` |
| `compose.nas.yaml` | `docker compose -f compose.nas.yaml pull`, then `docker compose -f compose.nas.yaml up -d` |
| The downloaded binary | Download the new one, put it in place of the old file (`mv` and `chmod +x` as before), and start it again |
| A binary you built | `git pull`, build again and restart |

The version is shown in the shortcuts list (press `?`).

## The commands an admin uses

| Command | What it does |
|---------|--------------|
| `./run.sh up` | Build and start Zone (also just `./run.sh`) |
| `./run.sh down` | Stop Zone |
| `./run.sh logs` | Follow Zone's log |
| `./run.sh build` | Build the image only |
| `./run.sh config check` | Each setting in effect, or what's wrong |
| `./run.sh config set KEY VALUE` | Set a key in `.zone/config`; `config unset KEY` for its default |
| `./run.sh config passwd` | The wiki's password, without users |
| `./run.sh config token` | The wiki's token for assistants, without users |
| `./run.sh user add NAME` | A new user; `--role viewer` or `--role admin` for another role |
| `./run.sh user passwd NAME` | A new password for a user |
| `./run.sh user token NAME` | A new token for a user's assistant; the old one stops working |
| `./run.sh user role NAME ROLE` | `admin`, `editor` or `viewer` |
| `./run.sh user email NAME ADDRESS` | A user's mail address |
| `./run.sh user group NAME +dev -design` | Put a user in a group, or take them out |
| `./run.sh user disable NAME` | They can't sign in; `user enable NAME` undoes it |
| `./run.sh user remove NAME` | Remove a user; their private pages stay on disk |
| `./run.sh user list` | The users, their roles and who has a token |
| `./run.sh group list` | The groups and who is in each |

A running Zone takes each change within a second.
All of them are in [Commands](../spec/users.md#commands).
