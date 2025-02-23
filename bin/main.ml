open! Core

(* https://ocaml.org/p/mrmime/latest/doc/Mrmime/Mail/index.html *)
let rec print_part (part : string Mrmime.Mail.t) =
  match part with
  | Leaf c -> printf "content:\n%s\n" c
  | Multipart p ->
    printf "multipart:\n";
    List.iter p ~f:(fun (h, oc) ->
      printf "multipart header:-\n";
      List.iter (Mrmime.Header.to_list h) ~f:(Mrmime.Field.pp Format.std_formatter);
      match oc with
      | Some p -> print_part p
      | None -> ())
  | Message (h, c) ->
    printf "embedded message:\n";
    printf "embedded message header:\n";
    List.iter (Mrmime.Header.to_list h) ~f:(Mrmime.Field.pp Format.std_formatter);
    print_part c
;;

(* https://ocaml.org/p/angstrom/latest/doc/Angstrom/index.html *)
let parse_print raw =
  let parser = Mrmime.Mail.mail None in
  match Angstrom.parse_string ~consume:All parser raw with
  | Ok (h, c) ->
    printf "header:\n";
    List.iter (Mrmime.Header.to_list h) ~f:(Mrmime.Field.pp Format.std_formatter);
    print_part c
  | Error e -> failwith ("cannot parse email: " ^ e)
;;

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
  let topics = Hey.Topic.all_topics api.imbox in
  let tids = topics |> List.map ~f:(fun (x : Hey.Topic.t) -> x.id) in
  let entries = Hey.Topic.all_entries (List.hd_exn tids) in
  let msgs =
    List.map ~f:(fun x -> Hey.Api.mk (Printf.sprintf "/messages/%Ld.txt" x)) entries
  in
  let raw = Hey.invoke (List.hd_exn msgs) in
  parse_print raw
;;
