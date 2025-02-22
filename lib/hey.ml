open! Core
module Protocol = Protocol.Protocol

module Hey (Args : sig
    val domain : string
    val token : string
  end) =
struct
  module Message = struct
    (* currently no plan for attachments *)
    type t =
      { id : int64
      ; time : Ptime.t option
      ; subject : string option
      ; sender : string option
      ; receiver : string list option
      ; cc : string list option
      ; bcc : string list option
      ; body : string option
      }
  end

  module Api = struct
    let mk ?(protocol = Protocol.HTTPS) path =
      Uri.of_string
        (Printf.sprintf "%s://%s%s" (Protocol.to_string protocol) Args.domain path)
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
      { cable : Uri.t
      ; imbox : Uri.t
      ; topics : Topic.t
      }

    let instance = ref None

    let create () =
      { cable = mk ~protocol:Protocol.WSS "/cable"
      ; imbox = mk "/imbox"
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
    let parse_one_exn soup =
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
    let parse_many_exn soup =
      Soup.select "article.posting" soup |> Soup.to_list |> List.map ~f:parse_one_exn
    ;;

    (** [next_page soup] return the next page uri if exists
        @param soup root of the parsed html
        @return uri option
        *)
    let next_page soup =
      soup
      |> Soup.select_one "a.pagination-link[data-pagination-target='nextPageLink']"
      |> Option.bind ~f:(Soup.attribute "href")
      |> Option.map ~f:(fun path -> Api.mk path)
    ;;
  end

  let invoke ?(verb = Protocol.HTTPS.GET) ?(body = "") uri =
    match Protocol.of_uri uri with
    | Some Protocol.HTTPS ->
      let resp, body =
        Cohttp_lwt_unix.Client.call
          ~headers:
            (Cohttp.Header.init_with
               "Cookie"
               (Printf.sprintf "session_token=%s" Args.token))
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
    | Some Protocol.WSS -> failwith "wss not implemented"
    | _ -> failwith "unsupported protocol"
  ;;
end
