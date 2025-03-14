open! Core
module Protocol = Protocol.Protocol

module Hey (Args : sig
    val cfg : Config.Config.t
  end) =
struct
  let invoke ?(verb = Protocol.HTTPS.GET) ?(body = "") uri =
    match Protocol.of_uri uri with
    | Some Protocol.HTTPS ->
      let resp, body =
        Cohttp_lwt_unix.Client.call
          ~headers:(Cohttp.Header.init_with "Cookie" Args.cfg.cookie)
          (Cohttp.Code.method_of_string (Protocol.HTTPS.to_string verb))
          uri
          ?body:
            (if String.is_empty body then None else Some (Cohttp_lwt.Body.of_string body))
        |> Lwt_main.run
      in
      let code = Cohttp.Response.status resp |> Cohttp.Code.code_of_status in
      if code >= 200 && code < 300
      then Cohttp_lwt.Body.to_string body |> Lwt_main.run
      else failwithf "request failed with status %d" code ()
    | _ -> failwith "unsupported protocol"
  ;;

  module Api = struct
    let mk ?(protocol = Protocol.HTTPS) path =
      Uri.of_string
        (Printf.sprintf "%s://%s%s" (Protocol.to_string protocol) Args.cfg.domain path)
    ;;

    module Topic = struct
      (** [topic t] creates an endpoint for a topic (thread)
          @param t either [`Name string] for named topics or [`Id int64] for topic id
          @return uri
      *)
      let topic = function
        | `Name t -> mk (Printf.sprintf "/topics/%s" t)
        | `Id t -> mk (Printf.sprintf "/topics/%Ld" t)
      ;;

      type t =
        { drafts : Uri.t
        ; sent : Uri.t
        ; spam : Uri.t
        ; trash : Uri.t
        ; everything : Uri.t
        ; topic : int64 -> Uri.t
        }

      let create () =
        { drafts = topic (`Name "drafts")
        ; sent = topic (`Name "sent")
        ; spam = topic (`Name "spam")
        ; trash = topic (`Name "trash")
        ; everything = topic (`Name "everything")
        ; topic = (fun id -> topic (`Id id))
        }
      ;;
    end

    type t =
      { imbox : Uri.t
      ; the_feed : Uri.t
      ; paper_trail : Uri.t
      ; reply_later : Uri.t
      ; set_aside : Uri.t
      ; bubble_up : Uri.t
      ; topics : Topic.t
      }

    let instance = ref None

    let create () =
      { imbox = mk "/imbox"
      ; the_feed = mk "/feedbox"
      ; reply_later = mk "/reply_later"
      ; paper_trail = mk "/paper_trail"
      ; set_aside = mk "/set_aside"
      ; bubble_up = mk "/bubble_up"
      ; topics = Topic.create ()
      }
    ;;

    let init () =
      match !instance with
      | Some _ -> ()
      | None -> instance := Some (create ())
    ;;

    let instance () =
      match !instance with
      | Some api -> api
      | None ->
        init ();
        Option.value_exn !instance
    ;;
  end

  module Topic = struct
    type t =
      { id : int64
      ; title : string
      ; time : Ptime.t
      }

    let to_string { id; title; time } =
      Printf.sprintf "%Ld %s %s" id (Ptime.to_rfc3339 time) title
    ;;

    (** [messages id] return a list of all raw messages (email) URIs in a topic (thread)
          @param id msg id
          @return uri
    *)
    let messages id = [] (* TODO *)

    (** [attachments id] return a list of all attachment URIs in a topic
          @param id topic id
          @return uri
    *)
    let attachments id = [] (* TODO *)

    (** [parse_one soup] parse a single topic from an article tag, throws if failed
        @param soup a soup node of an article tag
        @return parsed topic
    *)
    let parse_topic_exn soup =
      try
        let id =
          soup
          |> Soup.select_one "a.posting__link"
          |> Option.bind ~f:(Soup.attribute "href")
          |> Option.bind ~f:(fun x -> String.split ~on:'/' x |> List.last)
          |> Option.bind ~f:(fun x -> Int64.of_string x |> Option.some)
          |> Option.value_exn
        in
        let title =
          soup
          |> Soup.select_one "span.posting__title"
          |> Option.map ~f:(fun span -> Soup.texts span |> String.concat)
          |> Option.value_exn
          |> String.strip
        in
        let time =
          soup
          |> Soup.select_one "time"
          |> Option.bind ~f:(Soup.attribute "datetime")
          |> Option.bind ~f:(fun x -> Ptime.of_rfc3339 x |> Result.ok)
          |> Option.value_exn
          |> fst3
        in
        { id; title; time }
      with
      | _ -> failwith "cannot parse email"
    ;;

    (** [parse_many soup] return a list of all topics from a parsed html, throws if failed
        @param soup root of the parsed html
        @return a list of parsed topics
    *)
    let parse_topics_exn soup =
      Soup.select "article.posting" soup |> Soup.to_list |> List.map ~f:parse_topic_exn
    ;;

    let parse_entry_exn soup =
      soup
      |> Soup.attribute "data-entry-id"
      |> Option.bind ~f:Int64.of_string_opt
      |> Option.value_exn
    ;;

    let parse_entries_exn soup =
      Soup.select "article.entry" soup |> Soup.to_list |> List.map ~f:parse_entry_exn
    ;;

    (** [next soup] return the next pagination uri if exists
        @param soup root of the parsed html
        @return uri option
    *)
    let next soup =
      soup
      |> Soup.select_one "a.pagination-link[data-pagination-target='nextPageLink']"
      |> Option.bind ~f:(Soup.attribute "href")
      |> Option.map ~f:(fun path -> Api.mk path)
    ;;

    let all_topics topic =
      let rec aux acc = function
        | None -> acc
        | Some uri ->
          let soup = invoke uri |> Soup.parse in
          let next = next soup in
          aux (acc @ parse_topics_exn soup) next
      in
      let fst = invoke topic |> Soup.parse in
      let nxt = next fst in
      aux (parse_topics_exn fst) nxt
    ;;

    let all_entries topic =
      let rec aux acc = function
        | None -> acc
        | Some uri ->
          let soup = invoke uri |> Soup.parse in
          let next = next soup in
          (match parse_entries_exn soup with
           | [] -> acc
           | entries -> aux (acc @ entries) next)
      in
      let soup =
        Api.mk (Printf.sprintf "/topics/%Ld/entries" topic) |> invoke |> Soup.parse
      in
      let next = next soup in
      aux (parse_entries_exn soup) next
    ;;
  end

  module Entry = struct
    type t =
      { id : int64
      ; time : Ptime.t option
      ; sender : string option
      ; directly : string list option
      ; copied : string list option
      ; blindcopied : string list option
      ; subject : string option
      ; content : string option
      }

    let send e =
      let fire ~subject ~content ~directly ~copied ~blindcopied =
        let uri = Api.mk "/messages" in
        let build_recipients_data prefix emails =
          match emails with
          | [] -> []
          | emails ->
            List.map emails ~f:(fun email ->
              Printf.sprintf "entry[addressed][%s][]=%s" prefix (Uri.pct_encode email))
        in
        let directly_data = build_recipients_data "directly" directly in
        let copied_data = build_recipients_data "copied" copied in
        let blindcopied_data = build_recipients_data "blindcopied" blindcopied in
        let form_data =
          String.concat
            ~sep:"&"
            ([ Printf.sprintf "acting_sender_id=%d" Args.cfg.asid
             ; Printf.sprintf "acting_sender_email=%s" (Uri.pct_encode Args.cfg.user)
             ; Printf.sprintf "message[subject]=%s" (Uri.pct_encode subject)
             ; Printf.sprintf "message[content]=%s" (Uri.pct_encode content)
             ; "_method=POST"
             ; "commit=Send%20email"
             ]
             @ directly_data
             @ copied_data
             @ blindcopied_data)
        in
        let headers =
          Cohttp.Header.of_list
            [ "Content-Type", "application/x-www-form-urlencoded;charset=UTF-8"
            ; "Cookie", Args.cfg.cookie
            ; "X-CSRF-Token", Args.cfg.csrf
            ]
        in
        let resp, body =
          Cohttp_lwt_unix.Client.post
            ~headers
            ~body:(Cohttp_lwt.Body.of_string form_data)
            uri
          |> Lwt_main.run
        in
        let code = Cohttp.Response.status resp |> Cohttp.Code.code_of_status in
        if code = 302
        then Ok (Cohttp_lwt.Body.to_string body |> Lwt_main.run)
        else Error (Printf.sprintf "failed to send: HTTP %d" code)
      in
      match e.subject, e.content, e.directly with
      | Some subject, Some content, Some directly ->
        let copied = Option.value ~default:[] e.copied in
        let blindcopied = Option.value ~default:[] e.blindcopied in
        fire ~subject ~content ~directly ~copied ~blindcopied
      | _ -> Error "missing required fields (subject/content/recipient)"
    ;;
  end
end
