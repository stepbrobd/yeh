open! Core
module Config = Yeh.Config.Config
module Hey = Yeh.Hey.Hey

let () =
  let cfg = Config.instance () in
  printf "domain: %s\n" cfg.domain;
  printf "user: %s\n" cfg.user;
  printf "pass: %s\n" cfg.pass;
  printf "token: %s\n" cfg.token;
  printf "imap host: %s\n" cfg.imap.host;
  printf "imap port: %d\n" cfg.imap.port;
  printf "smtp host: %s\n" cfg.smtp.host;
  printf "smtp port: %d\n" cfg.smtp.port;
  let module H =
    Hey (struct
      let token = cfg.token
      let domain = cfg.domain
    end)
  in
  let api = H.Api.instance () in
  printf "cable: %s\n" (Uri.to_string api.cable);
  printf "imbox: %s\n" (Uri.to_string api.imbox);
  printf "drafts: %s\n" (Uri.to_string api.topics.drafts);
  printf "sent: %s\n" (Uri.to_string api.topics.sent);
  printf "spam: %s\n" (Uri.to_string api.topics.spam);
  printf "trash: %s\n" (Uri.to_string api.topics.trash);
  printf "everything: %s\n" (Uri.to_string api.topics.everything);
  api.invoke api.topics.everything |> Soup.to_string |> print_endline
;;
