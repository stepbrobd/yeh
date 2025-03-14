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
  (* let api = Hey.Api.instance () in
  let topics = Hey.Topic.all_topics api.imbox in
  let tids = topics |> List.map ~f:(fun (x : Hey.Topic.t) -> x.id) in
  let entries = Hey.Topic.all_entries (List.hd_exn tids) in
  let msgs =
    List.map ~f:(fun x -> Hey.Api.mk (Printf.sprintf "/messages/%Ld.txt" x)) entries
  in
  let raw = Hey.invoke (List.hd_exn msgs) in
  parse_print raw *)
  printf "subject: ";
  Out_channel.flush Out_channel.stdout;
  let subject = In_channel.input_line In_channel.stdin |> Option.value ~default:"" in
  printf "to (, separated): ";
  Out_channel.flush Out_channel.stdout;
  let recp_str = In_channel.input_line In_channel.stdin |> Option.value ~default:"" in
  let recp =
    String.split ~on:',' recp_str
    |> List.map ~f:String.strip
    |> List.filter ~f:(fun s -> not (String.is_empty s))
  in
  printf "cc (, separated, empty for none): ";
  Out_channel.flush Out_channel.stdout;
  let cc_str = In_channel.input_line In_channel.stdin |> Option.value ~default:"" in
  let cc =
    String.split ~on:',' cc_str
    |> List.map ~f:String.strip
    |> List.filter ~f:(fun s -> not (String.is_empty s))
  in
  printf "bcc (, separated, empty for none): ";
  Out_channel.flush Out_channel.stdout;
  let bcc_str = In_channel.input_line In_channel.stdin |> Option.value ~default:"" in
  let bcc =
    String.split ~on:',' bcc_str
    |> List.map ~f:String.strip
    |> List.filter ~f:(fun s -> not (String.is_empty s))
  in
  printf "content (end with only '.'): \n";
  Out_channel.flush Out_channel.stdout;
  let rec read_lines acc =
    let line = In_channel.input_line In_channel.stdin |> Option.value ~default:"" in
    if String.equal line "." then acc else read_lines (acc ^ line ^ "\n")
  in
  let content = read_lines "" in
  let email =
    Hey.Entry.
      { id = 0L
      ; time = None
      ; sender = None
      ; directly = Some recp
      ; copied = (if List.is_empty cc then None else Some cc)
      ; blindcopied = (if List.is_empty bcc then None else Some bcc)
      ; subject = Some subject
      ; content = Some content
      }
  in
  match Hey.Entry.send email with
  | Ok _ ->
    printf "done\n";
    Out_channel.flush Out_channel.stdout
  | Error e ->
    printf "failed: %s\n" e;
    Out_channel.flush Out_channel.stdout
;;
