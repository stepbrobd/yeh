open! Core
module Config = Yeh.Config.Config
module Hey = Yeh.Hey.Hey

let () =
  let cfg = Config.instance () in
  printf "user: %s\n" cfg.user;
  printf "pass: %s\n" cfg.pass;
  printf "token: %s\n" cfg.token;
  printf "imap host: %s\n" cfg.imap.host;
  printf "imap port: %d\n" cfg.imap.port;
  printf "smtp host: %s\n" cfg.smtp.host;
  printf "smtp port: %d\n" cfg.smtp.port;
  let module H = Hey (struct end) in
  printf "cable: %s\n" H.Api.cable;
  printf "imbox: %s\n" H.Api.imbox;
  printf "drafts: %s\n" H.Api.Topics.drafts;
  printf "sent: %s\n" H.Api.Topics.sent;
  printf "spam: %s\n" H.Api.Topics.spam;
  printf "trash: %s\n" H.Api.Topics.trash;
  printf "everything: %s\n" H.Api.Topics.everything
;;
