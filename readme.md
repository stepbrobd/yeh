# YEH <-> HEY

Roadmap:

HEY server renders all emails, they dont do client side fetching, except for
~~keeping a websocket connection open for new incoming emails and sending
emails~~.

Users/potential contributors: copy the session token (in cookie) from a browser
with HEY logged in and export environment variable for testing and deployment. I
plan to implement the IMAP/SMTP proxy to be a single tenant server, i.e. you'll
need two secrets to run a deployment (one for accessing HEY, one to login from
your email client).

Roadmap:

- [x] config parsing
- [x] can fetch raw html from imbox, drafts, sent, etc
  - [x] synchronously
  - [ ] async request with lwt?
- [ ] ~~websocket connection for sending emails~~
- [ ] smtp server (easy? state machine like)
- [ ] imap server (hard? use sqlite to prevent fetching all emails over and
      over)
- [ ] nixos module

IMAP:

- mailbox mapping, i.e. imbox -> inbox, collections -> folders, etc
- do html parsing, get message link, forward the raw email (from view original)
  to clients
- ~~keep websocket connection open for new incoming emails (or just poll every x
  seconds?)~~

SMTP:

- simple state machine, allow concurrent sends
- recieve commands from smtp clients and relay to hey's web interface sending
  logic (should be a simple http request)

Conventional commit:

- [bin|lib|doc|...]/<module>: regular code changes
- git: git related
- nix: nix related
- ci: automation stuff
- treewide: changes that affect multiple directories
- misc: anything else

## Notes

After getting HTML from Imbox or other top level folders (under /topics/*),
`#main-content > div > div > div > section` contains a list of all summary cards
of emails in article tags (avatar, subject, snippet, time), and an a tag at the
very end containing the link to next page
(`#main-content > div > div > div > section > a`)

`https://app.hey.com/messages/<id>.text` have the full email content (with
headers)

Maybe read from response header
(`x-ratelimit: {"name":"General","period":60,"limit":1000,"remaining":998,"until":"2025-02-22T04:22:00Z"}`)
to prevent getting rate limited?

Emails in screener will be put into Everything, no need to parse separately.

Topics are email threads, entries are emails in thread, different IDs.

Is parsing even needed after getting `<domain>/messages/<id>.text`? Directly
feed to IMAP downstream? Attachments are included in multipart?

To send email, need 2 CSRF tokens and HEY session token, and POST the form data
to submission endpoint:

```
# sender info
acting_sender_id=<HEY account ID>
acting_sender_email=<local part>@hey.com

# recipient info (if multiple, repeat the key)
entry[addressed][directly][]=local@example.com
entry[addressed][copied][]=local@example.com
entry[addressed][blindcopied][]=local@example.com

# email
message[subject]=Test
message[content]=<div></div>

# metadata
_method=post
commit=Send email
autodraft=false
entry[status]=drafted
entry[scheduled_delivery]=false
entry[scheduled_delivery_at_date]=<YYYY-MM-DD>
entry[scheduled_delivery_at_hour]=<0-24>
entry[scheduled_bubble_up]=false
entry[scheduled_bubble_up_on]=<YYYY-MM-DD>
```

```sh
cookie=$(sops decrypt --extract '["cookie"]' .env.yaml)
csrf=$(sops decrypt --extract '["csrf"]' .env.yaml)
asid=$(sops decrypt --extract '["asid"]' .env.yaml)
user=$(sops decrypt --extract '["user"]' .env.yaml)

curl -i "https://app.hey.com/messages" \
  -X 'POST' \
  -H 'Content-Type: application/x-www-form-urlencoded;charset=UTF-8' \
  -H "Cookie: $cookie" \
  -H "X-CSRF-Token: $csrf" \
  --data-urlencode "acting_sender_id=$asid" \
  --data-urlencode "acting_sender_email=$user" \
  --data-urlencode 'entry[addressed][directly][]=<test email address>' \
  --data-urlencode 'message[subject]=test subject' \
  --data-urlencode 'message[content]=sup this is some test content' \
  --data-urlencode '_method=POST' \
  --data-urlencode 'commit=Send email'
```

For the 2 CSRF tokens, one must be in the cookie, another in request header
(X-CSRF-Token), I assume the one in header is from Cloudflare and the one in
cookie is from HEY.

Don't need websokect for sending, and also don't need to check emails, just poll

## License

Licensed under the [MIT License](license.txt), not sure if this is a violation
of HEY's TOS, use at your own risk.

I'll keep working on this and maintain this if they don't provide official
IMAP/SMTP support (outrageous for a paid email service).
