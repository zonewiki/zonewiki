---
tags: project, radwerk
tldr: Spare parts are counted in a spreadsheet; this replaces it with labelled bins and a reorder list.
---
# Inventory system

## Problem

Parts run out unnoticed: twice in February a customer waited a week for a
brake pad that should have been on the shelf. The spreadsheet is only as
current as the last person who remembered it.

## Approach

Every bin gets a label with a part number and a minimum. Whoever takes the
last piece above the minimum moves the bin's card to the reorder board.
Jonas orders from the board on Tuesdays and Fridays.

## Part numbers

`BR-` brakes, `CH-` chains and cassettes, `TY-` tyres and tubes, `LT-` lights,
`MS-` everything else. The number after it is the bin.

## Decisions

- Cards and a board, not an app: the workshop has greasy hands and no reception.
- Minimums are set from last year's invoices, reviewed each October.

Tickets: [[Tickets/INV-2031]], [[Tickets/INV-2040]].
