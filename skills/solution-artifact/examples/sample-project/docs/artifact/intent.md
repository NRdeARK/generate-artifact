# Status Board · intent

## What
A web page where anyone on the team sees the latest status of every project in one place, updated within seconds of a change.

## Why
Status lives in chat threads and a weekly spreadsheet. People ask the same "where are we?" questions and read stale numbers.

## Before state
Project leads paste status into a spreadsheet once a week. A cron job turns it into an email export.
- Status is up to 7 days old
- Nobody knows who changed what
- The export breaks when a column moves

## Constraints
- Budget: stay inside the existing AWS account, no new vendors
- Time: first usable version in 4 weeks
- Must not expose project data outside the company network

## Outcome success
Anyone opens one page and trusts the status shown. A change by a project lead appears for everyone in under 10 seconds.
