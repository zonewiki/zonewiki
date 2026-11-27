# Importing notes

You can bring a folder of markdown notes into Zonewiki, an Obsidian vault for one: its notes become pages in a new folder, with the images and files they link to, and their links keep working.
You see what will come over before anything is written, and one click undoes the whole import.

## Importing a zip

1. Zip the folder: on a Mac, right-click it and choose **Compress**; on Windows, **Send to** › **Compressed (zipped) folder**; on Linux, `zip -r my-vault.zip "My Vault"`.
2. Open **Import pages**: press `Ctrl+P` (`⌘P` on a Mac), type "import" and choose **Import pages**.
3. Choose the zip under **Zip file**.
   **Folder** is where the pages go: leave it empty for a new folder named after the zip (`My Vault.zip` becomes `my-vault`).
   With users, **For** says who sees them: everyone, a group you are in, or only you.
4. Click **Upload**.

The folder must be a new one: a folder that is there already is refused, so nothing an import writes clashes with what you have.
The zip may be as large as an upload (100 MB unless whoever runs the wiki set another limit).

## The report

After the upload Zonewiki shows what it would import, and nothing is in the wiki yet:
how many pages and files go into which folder, whether it is an Obsidian vault, and what changed on the way:

| Heading | What it lists |
|---|---|
| **Renamed** | Notes whose page has another name than the note: two notes of one name, a name a page can't have |
| **Links with several targets** | A link that could mean several notes, and the one chosen |
| **Links to nothing** | A link to a note or file that isn't in the zip, left as written |
| **Lost on the way** | What has no equivalent in Zonewiki: block references, embedded notes, Dataview |
| **Left out** | Files no note links to, canvases, drawings |

Click **Import** to write the pages, or **Cancel** to drop the upload.

A note called `AGENTS.md` or `CLAUDE.md` would become a guide for AI assistants in the wiki, which only an admin writes: it is imported as `agents-` or `claude-` instead, and the report says so.

## After the import

Import opens the folder's index page: it lists every imported page and keeps the report.
Your home page (the private one, or the group's, for pages only you or a group see) gets a line linking it.

The import is one change in **Changes**: **Undo** there takes all of it back, the pages, the files and the line on the home page.

Zonewiki keeps what you uploaded only until you click **Import** or **Cancel**, an hour at most: then it is gone, and you upload the zip again.

The full rules, how notes, links, tags and callouts are converted, are in [Importing pages](../spec/editor.md#importing-pages).
