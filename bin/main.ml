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
  let all topic =
    let rec loop acc = function
      | None -> acc
      | Some uri ->
        let soup = Hey.invoke uri |> Soup.parse in
        let next = Hey.Topic.next_page soup in
        loop (acc @ Hey.Topic.parse_many_exn soup) next
    in
    let fst = Hey.invoke topic |> Soup.parse in
    let nxt = Hey.Topic.next_page fst in
    loop (Hey.Topic.parse_many_exn fst) nxt
  in
  all api.imbox |> List.iter ~f:(fun t -> printf "%s\n" (Hey.Topic.to_string t))
;;
