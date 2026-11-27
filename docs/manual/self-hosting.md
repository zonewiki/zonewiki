# Running Zonewiki yourself

This page is for whoever installs and runs a Zonewiki: on a laptop, a home server or a NAS.
It covers installing Zonewiki, where its settings live, setting a login, reaching it from other devices, and what to do before you open it to the internet.
The full details are in [the deployment spec](../spec/deployment.md) and [the security spec](../spec/security.md).

There are four ways to run it; pick the first that fits:

| Way | For | You need |
|-----|-----|----------|
| [With the binary](#with-the-binary) | Your own computer, the quickest start | git; Linux or a Mac (Windows: WSL2) |
| [With Docker](#with-docker) | Your computer, a server or a NAS, in a container, from the prebuilt image | Docker or Podman, with Compose |
| [From the repository](#from-the-repository) | The container built from a checkout with `./run.sh`: to change Zonewiki, or for search by meaning | git, and Docker or Podman with Compose |
| [From source](#from-source) | Building the binary yourself, to change Zonewiki or for another system | Go, a C compiler and git |

Whichever you choose, the wiki is a folder of Markdown files, so you can change the way later and keep the wiki.

This page writes the commands as `./run.sh …` and puts settings in `.env`, as Zonewiki in a container has them, with Docker and from the repository alike.
With the binary, a command is `./zonewiki …` (`./zonewiki config passwd`, `./zonewiki user add anna`),
and a setting is an environment variable you give it at the start, since the binary reads no `.env`; [Where settings live](#where-settings-live) shows how to keep several.

## With the binary

The quickest way to run Zonewiki on your own computer, on Linux or a Mac: one file, nothing to build.
Zonewiki needs git, for the wiki's history.
On a Mac, `xcode-select --install` installs it (or `brew install git`); on Linux, your package manager does (`sudo apt install git`).

1. Download the file for your computer from the [latest release](https://github.com/zonewiki/zonewiki/releases/latest):

   | Your computer | The file |
   |---------------|----------|
   | A Mac with Apple silicon (M1 or later) | `zonewiki-macos-arm64` |
   | A Mac with an Intel processor | `zonewiki-macos-amd64` |
   | Linux on a PC | `zonewiki-linux-amd64` |
   | Linux on ARM, such as a Raspberry Pi 4 or 5 (64-bit) | `zonewiki-linux-arm64` |
   | Windows | None: in WSL2, the Linux file for your processor |

2. In a terminal, name it `zonewiki`, make it a program, and start it:

   ```bash
   cd ~/Downloads
   mv zonewiki-macos-arm64 zonewiki      # the file you downloaded
   chmod +x zonewiki
   ./zonewiki
   ```

3. Open <http://localhost:5080>.

Your wiki is the folder `zonewiki` in your home folder, made at the first start; the terminal says where.
To keep it elsewhere, start Zonewiki with that folder.
To use a folder of Markdown notes you already have (kept in git or not), add `--init` the first time:

```bash
ZONE_DATA=~/Documents/notes ./zonewiki --init
```

Without it, Zonewiki won't start on a folder that holds files and isn't a wiki yet, and says what it found there: a typo in the folder's name could otherwise make your home folder a wiki.

Zonewiki runs as long as the terminal is open; `Ctrl+C` stops it, and your wiki stays as it is.

On a Mac, the binary isn't signed, so macOS won't open a downloaded one ("Apple could not verify…").
Take the download mark off it once, then start it again:

```bash
xattr -d com.apple.quarantine zonewiki
```

If port 5080 is taken, Zonewiki says so and stops.
Start Zonewiki on another port, `./zonewiki --addr :5081`, and open <http://localhost:5081>.

`./zonewiki --help` lists its commands and settings, `./zonewiki --version` says which version it is.
The binary searches by keywords, not by meaning; how it is built is in [Release binaries](../spec/deployment.md#release-binaries).

## With Docker

Zonewiki's image is prebuilt for amd64 and arm64, `ghcr.io/zonewiki/zonewiki`, so a computer, a server or a NAS with Docker (or Podman) and Compose runs it without the repository or a build.
It is built by Zonewiki's CI from the same `Containerfile` the repository builds from, so it is the same Zonewiki (searching by keywords only for now).

1. Create the wiki folder, empty or with Markdown files in it.
2. In another folder, put three files from the repository: `run.sh`, `compose.yaml` and `compose.podman.yaml` (only Podman needs the last).
3. Next to them, a `.env` that names the wiki folder.
4. Start Zonewiki with `./run.sh up`.

In a terminal:

```bash
mkdir -p ~/zonewiki ~/zonewiki-docker && cd ~/zonewiki-docker
base=https://raw.githubusercontent.com/zonewiki/zonewiki/main
curl -fsSL -O $base/run.sh -O $base/compose.yaml -O $base/compose.podman.yaml
chmod +x run.sh
echo 'ZONE_DATA=~/zonewiki' > .env
./run.sh up
```

Open <http://localhost:5080>; Zonewiki answers only on this computer.
The first start downloads the image.
Each `./run.sh` command says what it is doing in lines starting with `==>`; `./run.sh --help` lists them.
`./run.sh up` says **Zonewiki running at** once Zonewiki answers; if it doesn't start, it shows the end of Zonewiki's log instead, which says why.

`./run.sh` works out what compose can't: your user ID (so the wiki's files stay yours), your timezone, and whether to listen beyond localhost.
Beside `compose.yaml` it runs the prebuilt image; in a checkout of the repository it builds Zonewiki instead ([From the repository](#from-the-repository)).
To run compose without it, see [Without run.sh](../spec/deployment.md#without-runsh).

The wiki folder must exist before Zonewiki starts: Zonewiki won't create one where a typo points.
To use a folder of Markdown notes you already have, set `ZONE_DATA` in `.env` to it, and `ZONE_INIT=1` for the first start.
Zonewiki makes it a git repository if it isn't one, and adds a home page.
A folder inside another git repository (a folder of a project you keep in git) needs that repository to ignore it first, in its `.gitignore`: otherwise Zonewiki doesn't start, and says so.
What ends up in the folder is described in [Your data](your-data.md).

### On a NAS or server

For your phone or other computers to reach it, open it to the network in `.env`, and start Zonewiki again with `./run.sh up`:

```bash
ZONE_ALLOW_REMOTE=1
ZONE_PORT=5081          # optional: another port than 5080
```

Open it at `http://<the machine's address>:5080` (the port in `ZONE_PORT`, else 5080).
To open it by a name, tell Zonewiki the name: `./run.sh config set hosts nas.local`.
On the network Zonewiki asks for a password in the browser before it shows anything; or set a login first, `./run.sh user add anna`.

If the wiki folder belongs to another user than the one who runs `./run.sh`, name its owner in `.env`: `ZONE_UID` and `ZONE_GID` (`id -u` and `id -g` as that user).
For a Synology with Container Manager, step by step, see [Synology](../spec/deployment.md#synology).

## From the repository

Zonewiki's image built from a checkout, with `./run.sh`: for changing Zonewiki, for a branch, or for search by meaning, which the prebuilt image lacks for now.
You need git and Docker with Compose, or Podman with `podman-compose`.
Zonewiki is tested on Linux, and should work on macOS, and on Windows in WSL2.

```bash
git clone https://github.com/zonewiki/zonewiki && cd zonewiki
mkdir -p ~/zonewiki                   # your wiki folder
cp .env.example .env                  # ZONE_DATA is your wiki folder
./run.sh up                           # build and start
```

Open <http://localhost:5080>.
The first build takes a few minutes; later starts are quick.
Zonewiki is compiled inside the container's image, so no `zonewiki` file appears in the folder; `./run.sh binaries` builds the binaries for Linux and macOS into `dist/`, to run Zonewiki without a container.
Everything else is as [with Docker](#with-docker): the same commands, the same `.env`.

## From source

With Go 1.27 or newer, a C compiler and git:

```bash
(cd go && go build -o zonewiki .)
go/zonewiki
```

The wiki is then the folder `zonewiki` in your home folder, made at the first start.
As with the downloaded binary, no `.env` is read: settings are environment variables at the start.

```bash
ZONE_DATA=/path/to/wiki go/zonewiki                        # another wiki folder
ZONE_DATA=/path/to/wiki ZONE_ALLOW_REMOTE=1 go/zonewiki    # and open to the network
```

The commands are `zonewiki user …`, `zonewiki group …` and `zonewiki config …`, on the same wiki (with the same `ZONE_DATA`).
`zonewiki --help` lists them all.
Run by hand, Zonewiki listens only on the machine it runs on; `ZONE_ALLOW_REMOTE=1` opens it to the network, and it then asks for a login first.
See [From source](../spec/deployment.md#from-source), and for a Unix socket or systemd, [A Unix socket](../spec/deployment.md#a-unix-socket).

## Where settings live

Settings live in two places:

- **The environment**: how Zonewiki runs.
  The wiki folder, the timezone, network access, mail, a public wiki.
  In a container these are lines in the file `.env`, beside `run.sh`; a change takes effect at the next `./run.sh up`.
  The binary reads no `.env`: they are environment variables you start it with, and a change takes a restart.
- **`.zone/config`**, in the wiki folder: the wiki itself.
  Its users and their roles, the login, the names it's reached by, limits and everything else.
  Zonewiki reloads it within a second of a change, so no restart is needed.

An environment setting, in a container and with the binary:

```bash
# In a container: a line in .env, then ./run.sh up
ZONE_DATA=/home/anna/notes
ZONE_ALLOW_REMOTE=1
```

```bash
# With the binary: before the command
ZONE_DATA=~/notes ZONE_ALLOW_REMOTE=1 ./zonewiki
```

With more than a few, keep them in a small script and start Zonewiki with it (`sh start-zone.sh`):

```bash
#!/bin/sh
export ZONE_DATA="$HOME/notes"
export ZONE_ALLOW_REMOTE=1
export TZ=Europe/Vienna
exec ./zonewiki "$@"
```

The commands need the same wiki folder: `ZONE_DATA=~/notes ./zonewiki user add anna`, or `sh start-zone.sh user add anna`.

Change `.zone/config` with `./run.sh config set KEY VALUE` (and `config unset KEY` for the default), or edit it by hand.
A setting Zonewiki doesn't know, or a value that doesn't check, is an error that names its line.
A running Zonewiki then keeps the settings it had and says so in its log; a Zonewiki starting up refuses to start until it is fixed.
`./run.sh config check` shows each setting in effect and where it comes from.

### The settings people change most

| Setting | Where | What it does |
|---------|-------|--------------|
| `ZONE_DATA` | Environment | The wiki folder (default `~/zonewiki`) |
| `ZONE_INIT` | Environment | `1`: make a folder that holds files a wiki (or `--init`) |
| `TZ` | Environment | The timezone, such as `Europe/Berlin` (default: the host's) |
| `ZONE_ALLOW_REMOTE` | Environment | `1`: other devices on the network reach Zonewiki |
| `ZONE_PUBLIC` | Environment | `view` or `edit`: people read, or edit, without signing in |
| `ZONE_SIGNUP`, `ZONE_SMTP_*` | Environment | Sign-up and the mail server ([Mail](#mail)) |
| `ZONE_PORT` | Environment | The host's port, in a container (default 5080) |
| `hosts` | `.zone/config` | Names Zonewiki is opened by: `wiki.example.org, nas.local` |
| `base-url` | `.zone/config` | Zonewiki's address as people know it: `https://wiki.example.org` |
| `git-remote` | `.zone/config` | A remote to push the history to ([Your data](your-data.md#backups)) |
| `locale` | `.zone/config` | How dates read and when weeks start: `de-AT` |
| `date-format` | `.zone/config` | `iso`: dates as `2026-11-19` |
| `upload-max` | `.zone/config` | The biggest upload (default `100MB`) |
| `storage-max` | `.zone/config` | How much the wiki may hold (default: no limit) |
| `external-content` | `.zone/config` | Sites pages may show images and frames from: `upload.wikimedia.org` |
| `trusted-proxies` | `.zone/config` | A reverse proxy's address, when it isn't on the same machine |
| `notice` | `.zone/config` | A line across the top of every page, for everyone: `Down for maintenance tonight at 22:00` ([The notice](../spec/users.md#the-notice)) |

Every environment setting is in `.env.example` with a comment (`ZONE_PORT` is the compose file's alone).
Every `.zone/config` key is in [The wiki's keys](../spec/users.md#the-wikis-keys).

## Setting a login

On localhost, out of the box, Zonewiki has no login: whoever can reach the port can do everything.
That's fine on your own laptop, and nowhere else.

To give it one, choose **Set a login** in the menu at the top right (the three lines).
As soon as other devices can reach Zonewiki, it asks by itself, and shows nothing until a login is set.
Either way it asks for a password (at least 8 characters, twice), then **Set the password**:

- leave **User name (optional)** empty, and the wiki gets one password;
- fill it in, and you become the wiki's first user and its admin, and can invite others.
  An assistant connected with the wiki's token is then connected again, as you.

Whoever reaches a new wiki first sets its login, so do this right after starting it.
If you meet a login you didn't set, remove it on the host, in `.zone/config`.

From the terminal it's the same:

```bash
./run.sh config passwd          # one password for the wiki
./run.sh user add anna          # or: a first user, who is an admin
```

Adding users, roles, groups and invites is in [Teams](teams.md).
To keep a wiki on the network open on purpose, without a login: `./run.sh config set no-login yes`.
Such a wiki gets a login only from the terminal: in the browser, anyone on the network could set one and lock the others out.

## Reaching it from other devices

By default Zonewiki listens only on the machine it runs on.
To reach it from your phone or another computer on the same network:

1. Set `ZONE_ALLOW_REMOTE=1` and start Zonewiki again:

   ```bash
   # In a container: add the line to .env, then
   ./run.sh up
   ```

   ```bash
   # With the binary
   ZONE_ALLOW_REMOTE=1 ./zonewiki
   ```

2. Open `http://<the machine's address>:5080`, such as `http://192.168.1.20:5080`.

Any IP address then works.
To open Zonewiki by a name instead (`nas.local`, `wiki.example.org`), add the name to `hosts`:

```bash
./run.sh config set hosts nas.local, wiki.example.org
```

A browser that opens Zonewiki by a name not in `hosts` gets an error page saying so, and the log names it.
This check stops other websites from reaching your wiki through your browser.

On a phone, Zonewiki can be added to the home screen as an app.
Android installs it as a real app only over HTTPS.

## Before you open it up

Plain `http://` is fine on localhost.
Anywhere else, read this first.

- **Use HTTPS.** Over plain `http://` your password, your login and your pages travel unencrypted: anyone on the same Wi-Fi can read them and sign in as you.
- **Set a login**, with a long, unique password. There is no second factor.
- **For just yourself, prefer a VPN** (WireGuard, Tailscale) to a public address.
- **Know who can read what.** Every user reads everything except other users' private pages and the pages of groups they aren't in. Private pages are hidden inside Zonewiki, not encrypted: whoever has the folder or its git history reads them all.
- **Make admin only people you trust with everyone's pages.**
- **Keep tokens secret.** An assistant's token can change and delete pages as its user can. You can connect an assistant that only reads ([AI assistants](assistants.md)).
- **Treat the wiki folder, its git remote and every backup as confidential.**

The full list is in [Before you open it up](../spec/security.md#before-you-open-it-up).

### HTTPS with a reverse proxy

Put a reverse proxy in front of Zonewiki that handles HTTPS.
[Caddy](https://caddyserver.com) on the same machine gets the certificate by itself:

```
wiki.example.org {
    reverse_proxy localhost:5080
}
```

Then tell Zonewiki its name and address, and set the login:

```bash
./run.sh config set hosts wiki.example.org
./run.sh config set base-url https://wiki.example.org
./run.sh config passwd
```

Leave `ZONE_ALLOW_REMOTE` off: the proxy reaches Zonewiki on localhost, and nobody can go around HTTPS to port 5080.
A proxy on another machine needs `ZONE_ALLOW_REMOTE=1`, and its address in `trusted-proxies` so that wrong passwords are counted per visitor, not all as the proxy's.
Any other proxy must pass on the original host name and say the request came over HTTPS (`X-Forwarded-Proto`).
See [Self-hosting behind HTTPS](../spec/security.md#self-hosting-behind-https).

## Mail

Zonewiki sends mail for two things: sign-up, and telling users what is new for them (a task given to them, a mention, a comment, or one summary every morning), for those who turned it on in My tasks' settings.
It needs an SMTP server, set in `.env` (with the binary, the same names as environment variables: [Where settings live](#where-settings-live)):

```bash
ZONE_SMTP_HOST=smtp.example.org
ZONE_SMTP_PORT=587
ZONE_SMTP_USER=wiki@example.org
ZONE_SMTP_PASSWORD=…
ZONE_SMTP_TLS=starttls             # starttls (default), tls (port 465) or none
ZONE_MAIL_FROM=wiki@example.org
```

Mail is always encrypted unless you set `ZONE_SMTP_TLS=none`, for a relay on the same machine.
Zonewiki won't start on a mail setting that doesn't make sense, and says why.

A user gets mail only once they have an address: sign-up records it, or an admin sets it with `./run.sh user email anna anna@example.org`.

### Sign-up

`ZONE_SIGNUP=1` (in `.env`, or `ZONE_SIGNUP=1 ./zonewiki`) lets people ask for an account with their mail address.
It also needs users and `base-url` in `.zone/config`.
There, `signup-domains` limits it to addresses at your domains, `signup-role` gives new accounts `viewer` (the default) or `editor`, and `signup-approval = yes` has an admin approve each one.
See [Sign-up](../spec/users.md#sign-up).

## A public wiki

`ZONE_PUBLIC=view` lets anyone read the wiki's shared pages without signing in, and `ZONE_PUBLIC=edit` lets them edit too; see [Teams](teams.md).

```bash
# In a container: a line in .env, then ./run.sh up
ZONE_PUBLIC=view
```

```bash
# With the binary
ZONE_PUBLIC=view ./zonewiki
```

## Updating Zonewiki

Your wiki is never touched by an update: it lives in its own folder.

| Installed with | To update |
|----------------|-----------|
| Docker, or the repository and `./run.sh` | `./run.sh update`: the newest image, or the newest code built |
| The downloaded binary | Download the new one, put it in place of the old file (`mv` and `chmod +x` as before), and start it again |
| A binary you built | `git pull`, build again and restart |

The version is shown in the shortcuts list (press `?`).

## The commands an admin uses

| Command | What it does |
|---------|--------------|
| `./run.sh up` | Start Zonewiki (also just `./run.sh`); from the repository, build it first |
| `./run.sh down` | Stop Zonewiki |
| `./run.sh logs` | Follow Zonewiki's log |
| `./run.sh update` | Update Zonewiki and start it again |
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
| `./run.sh user shared NAME` | Several people sign in to this account: its password and token can't be changed in the browser |
| `./run.sh user remove NAME` | Remove a user; their private pages stay on disk |
| `./run.sh user list` | The users, their roles and who has a token |
| `./run.sh group list` | The groups and who is in each |
| `./run.sh export > FILE` | The whole wiki in one file, to move it ([Moving to another Zonewiki](your-data.md#moving-to-another-zonewiki)) |
| `./run.sh import < FILE` | The wiki from such a file, into an empty wiki folder |

A running Zonewiki takes each change within a second.
All of them are in [Commands](../spec/users.md#commands).
