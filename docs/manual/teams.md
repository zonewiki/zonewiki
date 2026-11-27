# Teams

Zonewiki works for one person, and just as well for a team sharing one wiki.
This page explains how several people use one wiki: signing in, private pages, groups and roles.
The second half is for admins: adding people, invite links, sign-up and a public wiki.

## One wiki, with or without users

A wiki without users has one password for everyone, or no login at all.
Everything in it is shared, and every change is committed as Zonewiki's own, with no person's name.

A wiki with **users** gives each person their own user name and password.
Their changes are committed in their name, so a page's history says who wrote what.
An AI assistant they connect acts as them, and sees only what they see ([AI assistants](assistants.md)).
Each user also gets private pages, private notes and private tasks, which no one else sees.

An admin adds the users ([For admins](#for-admins), below).
If you run Zonewiki yourself, the first user you make is the admin.

## Signing in

Open the wiki, type your **User name** and **Password**, and click **Sign in**.
A wrong name or password says "Incorrect user name or password.", without saying which one was wrong.

Once signed in, the round button with your initial at the top right opens your **user menu**:
**Settings**, **Admin** (for admins only), **Sharing mode** (on a computer), **Help** and **Sign out**.
**Settings** opens three pages that are tabs of each other: **Assistant** ([connect an assistant](assistants.md)), **Password** and **Groups**.
On a phone, your name in the top row opens the same menu.

### Changing your password

Choose **Settings** in the user menu, then the **Password** tab (the `?` shortcuts dialog links to it too).
Type **Your password now**, then the new one twice, at least 8 characters, and click **Change password**.

Every other place you were signed in is signed out, so changing your password is also how you end a session someone else got hold of.
An assistant you connected stays connected; you end that on **Connect an assistant**.

Forgot your password? Zonewiki doesn't mail one: ask an admin for a reset link ([Reset links](#reset-links)).

An account several people share (a demo's) has no **Password** tab: see [A shared account](#a-shared-account).

## Where a page lives decides who sees it

In Zonewiki the folder a page is in is the only thing that decides who sees it.
Not its tags, not its links, not its name.

| Where | Who sees and changes it | Mark |
|-------|-------------------------|------|
| `private/<you>/` | only you | 🔒 |
| `groups/<name>/` | whoever is in the group | 👥 name |
| anywhere else | everyone who uses the wiki | none |

To anyone who may not see a page, it isn't there: it's left out of search, lists, backlinks and history, and opening its address says there is no such page.
A link to it shows as plain words.
Hover over a page's 🔒 or 👥 mark to see who sees it; on a phone a line under the title says it.

> [!WARNING]
> Private means hidden inside Zonewiki, from other users and their assistants.
> It is not encrypted: whoever has the wiki's folder or its git repository on the server can read everything.

### Your private pages

**Private** in the module switcher (or `g p`) opens your private home, a page only you see.
Link your private pages from it: a link to a page that doesn't exist yet opens the editor for it, in your private folder.

Other ways to make a private page:

- Link to it by its path from any page you write, for example `[[private/anna/diary]]`.
- Click **New page** on your private home: it is set to make a page only you see (**For** "Only you", or the **Private** box if you're in no group).
- File a note into your private folder ([Notes](notes.md)).

On a private page, a link to a new page is marked 🌐 ("Creates a shared page"), because a page made from it would be shared.
When you open it, the editor asks: **Make it private** points the link at a page in your folder instead, **Keep shared** leaves it.

You may link to a private page from a shared page.
You see a 🔒 after the link; everyone else sees plain text.

**Notes are always private**: a new note is yours alone until you move it to the wiki.
Files you upload to a private page are private too.

### A group's pages

A group has a folder, `groups/<name>/`, that only its members see and change.
If you're in a group, it has a pill in the module switcher (`g g`), which opens the group's home page.

Make a page for the group with **New page** beside the group on **Groups**, or from a link on one of its pages: there `[[` offers "New page for @dev".
A group's pages show **👥 dev** beside their title, and files you upload to them stay with the group.

### Private tasks and lists

Tasks follow the same idea, by list rather than folder:

- A new task starts in your **Inbox**, where only you see it.
- A list is private, a group's, or shared with everyone, as a page is: a group's list is kept in the group's folder.
  You choose when you make it with **New list…**'s **For**: everyone, one of your groups, or only you.
  Its owners move it later with **Who sees it…** in the list's ⋯ menu.
- Moving a task into a shared list shares it, and Zonewiki asks first.
  A task you share is assigned to you, unless you say who has it.
- A shared task can't be made private again: others have seen it.

[Tasks](tasks.md#lists) has the details.

## Groups

A **group** is a set of users with a name, such as `dev` or `q3-plan`: a team's or a project's own place.
What is for a group is its folder of pages and the task lists made for it, and only its people see them.
A project everyone may see needs no group: give it a page in the wiki, such as `projects/garden-shed`.
A group can also be made of other groups.

### Seeing your groups

**Settings** in the user menu, then the **Groups** tab, opens the Groups page (so does **All your groups** in the group pill's menu).
It lists the groups you are in or own: who is in each, its owners and how many pages it has.
Beside each are **New page** and **New list**, for a page or task list only that group sees.

### Making a group

Anyone who may write can make a group, without waiting for an admin.
Under **Make a group**, type a name (lowercase letters, digits and hyphens, at most 40), pick the other people, and click **Make the group**.

You are in the group and are its **owner**, so you say who else is in it.
Zonewiki then opens the group's home page, which asks what the group is for: edit it to say so in a line or two, for its people and their assistants.
A name that is taken can't be used again, including the former name of a renamed group.

People are picked rather than typed blind: as you type, Zonewiki offers matching users, and each pick becomes a pill with × to remove it.

### Owning a group

An owner sees **Manage…** beside the group, with **People** and **Owners**.
Change them and click **Save**.

- Taking someone out asks first ("Yes, take out bob"): they stop seeing the group's pages and lists at once.
- A group always keeps an owner: to step down, name another owner first.
- An owner doesn't have to be in the group, but then sees none of its pages.

### Leaving a group

Click **Leave…** beside a group, then **Leave dev**.
You stop seeing its pages and lists at once; its owners can put you back.

**Leave…** isn't there for a group you manage (take yourself out under **Manage…**), for a group without owners (an admin's), or for a group you are in only through another group.

### Renaming a group

Under **Manage…**, change **Name** and click **Rename**.
The group's pages and task lists move to the new folder, and every link to them follows.
The old name stays reserved for this group, so a rename can be undone.

### Ending a group

**Remove** at the foot of **Manage…** ends a group: its name comes off its people.
If the group has pages, you choose what becomes of them first:

- **Move the pages to the wiki**, into a folder you name (the group's name by default).
  Everyone who uses the wiki then sees them, and links follow.
- **Delete the pages** and their files.
  You can't bring them back in the browser; they stay in git on the server.

A task list that is for the group must be changed or deleted first, and the page says which.
If a page would land where a wiki guide pulls a page in, nothing moves: name another folder, or ask an admin.
The name stays taken while the group's folder has a history.

### Groups made of groups

When two teams share something, an admin can make a larger group out of them instead of naming everyone twice.
Under **A group of groups** on the Groups page, type a name, the groups it includes, and click **Make a group of groups**.
Whoever is in `dev` or `design` is then in `product` too, including anyone who joins a team later.

An admin changes what a group includes with **Everyone in** under its **Manage…**.
Taking a group out removes what is for the larger group from its people.

## Moving a page to more or fewer readers

Because the folder decides who sees a page, moving a page is how you share it.

**To more people**, whoever may write the page can move it.
On one of your private pages, **Share…** in the ⋯ menu offers each group you are in and **Everyone**, each with who will see the page.
On a group's page it offers everyone, and any group that includes this one.
(If you're in no group, the entry is **Make shared…**.)
The page moves, links to it follow, and so do the files it shows.

A plain rename into a wider place works too: Zonewiki says who will see the page and asks **Yes, move it**.

**To fewer people** only an admin may move a page, or whoever shared it as long as nobody else has changed it since (to undo a mistake of a minute ago).
Anyone else is told to ask an admin.
Links to it on pages others read keep the words they showed, which is all that those who no longer see the page read of them ([Links](../spec/editor.md#links)).

Why the difference: a move to fewer people takes the page away from people who had it, and they wouldn't see it go or be able to undo it.
Those left out keep the versions up to the move in the history, and links to it become plain text for them.
[The full rules](../spec/users.md#who-may-move-a-page-to-other-readers) are in the spec.

## Assigning tasks

A shared task can be assigned to one user, who does it.
Assign it in the task's dialog (**Assignee**), or end a new task's title with `@name`, as in `Call the printer @ben #work`.
Filter a list with `@anna`, `@me` or `@none`.
Assigning never changes who sees a task.
A private task is yours; to give it to someone else, name them and choose the shared list Zonewiki then asks for ([Giving away a task of your own](tasks.md#giving-away-a-task-of-your-own)).
[Tasks](tasks.md) explains assigning, My tasks and the news on your tasks.

## Roles

Each user has a role:

| Role | Can |
|------|-----|
| admin | everything an editor can, plus manage users and every group, write the guides, and move pages to fewer people |
| editor | read and change everything shared, and have private pages, notes and tasks (the default) |
| viewer | read everything shared, and change nothing, not even private pages or notes |

A viewer sees no Edit button, no Private or Notes, and no New task.
Someone who needs notes or tasks of their own should be an editor.

An admin is not a superuser.
No one, an admin neither, sees another user's private pages, notes or tasks, or the pages of a group they aren't in.

## For admins

### The Admin page

**Admin** in the user menu opens the Admin page, which only admins see.
Its **Users** tab lists every user with their role, groups and status (Active, Disabled, or Waiting for approval).
"MCP" beside a name means they have an assistant token.

In each row you can:

- change the **Role** and click **Save**;
- set their **Groups** (only groups there are; new groups are made on Groups) and click **Save**;
- **Disable** or **Enable** them, **Reset password**, or **Remove…** them.

You can't disable or remove yourself here, so you can't lock yourself out; another admin can.
You can change your own role, as long as another admin remains: a wiki always keeps one admin.

### Adding users

Add people with an invite link, or let them sign up themselves.
There is no field for typing someone else's password: you never know it.

### Invite links

Under **Invite someone**, type a user name, pick a role, and click **Make an invite link**.
Copy the link and send it any way you like: Zonewiki doesn't mail it, and shows it only once.

The link works once, within 7 days.
It opens a page that greets them by name and asks for a password twice; **Join** makes the account and signs them in.
Opening the link alone changes nothing, so a mail scanner can't use it up.

Open links are listed under **Open links**, with **Revoke** to take one back.
A new link for the same name replaces the old one.
A link stops working when the admin who made it is no longer an admin (made an editor, disabled or removed), and stays ended if they become admin again: make a new one then.

### Sign-up

Sign-up lets people ask for an account with their mail address.
Whoever runs the server turns it on and sets up mail ([Running Zonewiki yourself](self-hosting.md)).
When it's on, the login page links to it and the Users tab says so.

They type their address and click **Send the link**; the mail's link, valid 24 hours, asks for a user name and password (**Make my account**).
Three settings shape it: `signup-domains` takes only addresses at your domains, `signup-role` makes new accounts `viewer` (the default) or `editor`, and `signup-approval = yes` holds each account until you click **Approve** on the Users tab.
To turn someone down, remove them.

### Reset links

Zonewiki has no "forgot password" mail.
Click **Reset password** beside a user, and send them the link; it works once, within 24 hours.
Setting a new password with it signs them out everywhere and ends the connections of the assistants they allowed.

> [!NOTE]
> A reset link sets the password for whoever opens it, you included.
> So an admin could take over an account, but not unnoticed: the user's old password stops working.

### Disabling and removing users

**Disable** stops someone signing in and stops their assistant's token; their pages and tasks stay as they are.
**Enable** undoes it.

**Remove…** asks first, then removes the account.
Their private pages, notes and tasks stay in `private/<name>/`, seen by no one; someone added again under that name gets them back.
Their open invite or reset link goes too.

Tasks still assigned to a disabled user or a viewer show under their status as "2 open tasks to hand on", linked to the list where you select them and **Assign…** them to someone else.

### A shared account

For a demo, or a screen in the office, several people sign in to one account with one password.
Mark it with `./run.sh user shared NAME`, so that none of them locks the others out:
The **Password** tab is gone from its **Settings**, and **Connect an assistant** offers no token to make or revoke.
You set its password and token with `./run.sh user passwd NAME` and `./run.sh user token NAME`.

Each assistant that signs in through the browser is still a connection of its own.
A shared account can be an editor or a viewer, never an admin, and the **Users** tab marks it **shared**.
`./run.sh user shared NAME --remove` makes it one person's account again.
The full rules: [Shared accounts](../spec/users.md#shared-accounts).

### Mail addresses

A user's mail address is used for task mails and to tell sign-ups apart.
Sign-up records it; for anyone else, whoever runs the server sets it with a command (below).
There is no field for it in the browser.
Without an address a user gets no mail.

### Guides are admin-only

Guides are the pages every user's assistant follows: `wiki-guide`, and a folder's `AGENTS.md` or `CLAUDE.md` ([The wiki guide](wiki-guide.md)).
With users, only an admin may change them, because whoever writes a guide steers everyone's assistant.
A group's guide can be changed only by an admin who is in that group.

### Commands for self-hosting admins

If you run Zonewiki yourself, you can manage users from the server's command line as well.
These edit the wiki's settings file directly, so they need access to the server rather than the admin role.
Use `./run.sh user …` in the repository, or `zonewiki user …` without the container.

| Command | Does |
|---------|------|
| `./run.sh user add anna` | adds a user, asking for their password; the first user is an admin |
| `./run.sh user add ben --role viewer` | adds a viewer (or `admin`, `editor`) |
| `./run.sh user invite ben --role viewer` | prints an invite link |
| `./run.sh user reset anna` | prints a reset link |
| `./run.sh user passwd anna` | sets a new password |
| `./run.sh user role ben editor` | changes a role |
| `./run.sh user group ben +dev -design` | puts them in a group or takes them out |
| `./run.sh user email ben ben@example.org` | sets their mail address (`--remove` takes it out) |
| `./run.sh group includes product +dev +design` | makes a group of groups |
| `./run.sh group owners q3-plan +anna` | gives a group an owner |
| `./run.sh user disable ben` | disables them (`user enable` undoes it) |
| `./run.sh user shared demo` | marks an account several people sign in to (`--remove` undoes it) |
| `./run.sh user remove ben` | removes them; their private files stay |
| `./run.sh user list` | lists users, roles and who is disabled |

[Running Zonewiki yourself](self-hosting.md) covers the settings file and the rest of the commands.

## A public wiki

A wiki with users can also be shown to people who aren't signed in, called **visitors**.
Whoever runs the server sets this where Zonewiki is started (in `.env`, or as an environment variable for the binary: [Where settings live](self-hosting.md#where-settings-live)), not an admin in the browser:

| `ZONE_PUBLIC` | Visitors can |
|---------------|--------------|
| unset or `off` | nothing: everyone signs in (the default) |
| `view` | read the shared pages, search them and see their history |
| `edit` | also edit pages, make new ones and tick checkboxes |

Visitors never see tasks, notes, private pages or groups' pages, and can't upload, rename, delete or change a guide.
Their edits are saved as "anonymous".
A **Sign in** button at the top right lets users sign in as usual.

> [!WARNING]
> Everything shared is public then, with its whole history, and so is every uploaded file in the shared `_assets/` folder for whoever knows its name.
> Keep what isn't for everyone in private folders or group folders.
> A wiki strangers can edit should have a rate limit in the reverse proxy in front of it.
