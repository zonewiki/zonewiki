---
tags: howto, computer
---
# VPN to the home network

For reaching the NAS and the printer from outside.

## On a phone

Install the WireGuard app, scan the QR code from the envelope in the study's
drawer, and name the tunnel "Lindenhof".

## On a laptop

Import the file `lindenhof.conf` from the same envelope's USB stick. Delete
the file from the laptop afterwards.

## When it doesn't connect

The router's public address changes at night. The tunnel uses the name
`lindenhof.dyn.example`; if that fails, the dynamic DNS update on the router
has stopped: log in to the router and press "Update now".
