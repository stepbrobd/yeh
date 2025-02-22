open! Core

let () =
  let cfg = Yeh.Config.Config.instance () in
  let module Hey =
    Yeh.Hey.Hey (struct
      let token = cfg.token
      let domain = cfg.domain
    end)
  in
  let api = Hey.Api.instance () in
  (* printf "domain: %s\n" cfg.domain;
  printf "user: %s\n" cfg.user;
  printf "pass: %s\n" cfg.pass;
  printf "token: %s\n" cfg.token;
  printf "imap host: %s\n" cfg.imap.host;
  printf "imap port: %d\n" cfg.imap.port;
  printf "smtp host: %s\n" cfg.smtp.host;
  printf "smtp port: %d\n\n" cfg.smtp.port;

  printf "cable: %s\n" (Uri.to_string api.cable);
  printf "imbox: %s\n" (Uri.to_string api.imbox);
  printf "drafts: %s\n" (Uri.to_string api.topics.drafts);
  printf "sent: %s\n" (Uri.to_string api.topics.sent);
  printf "spam: %s\n" (Uri.to_string api.topics.spam);
  printf "trash: %s\n" (Uri.to_string api.topics.trash);
  printf "everything: %s\n\n" (Uri.to_string api.topics.everything); *)
  let print root =
    root
    |> Hey.Summary.parse
    |> List.map ~f:Hey.Summary.to_string
    |> List.iter ~f:(printf "%s\n")
  in
  let first = api.invoke api.topics.everything in
  let rec loop root =
    let next = api.next root in
    match next with
    | Some uri ->
      print root;
      loop (api.invoke uri)
    | None -> print root
  in
  loop first
;;
