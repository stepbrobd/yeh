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
  let module Hey =
    Yeh.Hey.Hey (struct
      let cfg = Yeh.Config.Config.instance ()
    end)
  in
  let api = Hey.Api.instance () in
  let topics = Hey.Topic.all_topics api.imbox in
  let tids = topics |> List.map ~f:(fun (x : Hey.Topic.t) -> x.id) in
  let entries = Hey.Topic.all_entries (List.hd_exn tids) in
  let msgs =
    List.map ~f:(fun x -> Hey.Api.mk (Printf.sprintf "/messages/%Ld.txt" x)) entries
  in
  let raw = Hey.invoke (List.hd_exn msgs) in
  parse_print raw
;;
