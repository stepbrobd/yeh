- hey server renders all emails, they dont do client side fetching, except for
  keeping a websocket connection open for new incoming emails
- auth: copy the bearer token from browser, single tenant (use a separate
  password for client side auth)
- imap
  - mailbox mapping, i.e. imbox -> inbox, collections -> folders, etc
  - do html parsing, get message link, forward the raw email (from view
    original) to clients
  - keep websocket connection open for new incoming emails (or just poll every x
    seconds?)
- smtp
  - simple state machine, allow concurrent sends
  - recieve commands from smtp clients and relay to hey's web interface sending
    logic (should be a simple http request)

conventional commit:

- [bin|lib|doc|...]/<module>: regular code changes
- git: git related
- nix: nix related
- ci: automation stuff
- treewide: changes that affect multiple directories
- misc: anything else
