open! Core
module Protocol = Protocol.Protocol

module Hey (Args : sig
    val domain : string
    val token : string
  end) =
struct
  (* merge with email *)
  module Summary = struct
    type t =
      { id : int64
      ; subject : string
      ; received : Ptime.t
      }

    let to_string { id; subject; received } =
      Printf.sprintf "%Ld %s %s" id (Ptime.to_rfc3339 received) subject
    ;;

    (* root must the result of an invoke *)
    let parse root =
      let once article =
        try
          let id =
            article
            |> Soup.select_one "a.posting__link"
            |> Option.bind ~f:(Soup.attribute "href")
            |> Option.bind ~f:(fun x -> String.split ~on:'/' x |> List.last)
            |> Option.bind ~f:(fun x -> Int64.of_string x |> Option.some)
            |> Option.value_exn
          in
          let subject =
            article
            |> Soup.select_one "span.posting__title"
            |> Option.map ~f:(fun span -> Soup.texts span |> String.concat)
            |> Option.value_exn
            |> String.strip
          in
          let received =
            article
            |> Soup.select_one "time"
            |> Option.bind ~f:(Soup.attribute "datetime")
            |> Option.bind ~f:(fun x -> Ptime.of_rfc3339 x |> Result.ok)
            |> Option.value_exn
            |> fst3
          in
          { id; subject; received }
        with
        | _ -> failwith "cannot parse email"
      in
      Soup.select "article.posting" root |> Soup.to_list |> List.map ~f:once
    ;;
  end

  module Email = struct
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
              (if String.is_empty body
               then None
               else Some (Cohttp_lwt.Body.of_string body))
          |> Lwt_main.run
        in
        let code = Cohttp.Response.status resp |> Cohttp.Code.code_of_status in
        if code >= 200 && code < 300
        then Cohttp_lwt.Body.to_string body |> Lwt_main.run |> Soup.parse
        else failwithf "request failed with status %d" code ()
      | Some Protocol.WSS -> failwith "wss not implemented"
      | _ -> failwith "unsupported protocol"
    ;;

    let next root =
      root
      |> Soup.select_one "a.pagination-link[data-pagination-target='nextPageLink']"
      |> Option.bind ~f:(Soup.attribute "href")
      |> Option.map ~f:(fun path -> mk path)
    ;;

    module Topics = struct
      (** [topic t] creates an endpoint for a topic
          @param t either [`Name string] for named topics or [`Id int64] for msg id
          @return uri
      *)
      let topic = function
        | `Name t -> mk (Printf.sprintf "/topics/%s" t)
        | `Id t -> mk (Printf.sprintf "/topics/%Ld" t)
      ;;

      (** [attachments id] creates an endpoint for email attachments
          @param id msg id
          @return uri
      *)
      let attachments id = mk (Printf.sprintf "/topics/%Ld/attachments" id)

      type t =
        { drafts : Uri.t
        ; sent : Uri.t
        ; spam : Uri.t
        ; trash : Uri.t
        ; everything : Uri.t
        ; topic : int64 -> Uri.t
        ; attachments : int64 -> Uri.t
        }

      let create () =
        { drafts = topic (`Name "drafts")
        ; sent = topic (`Name "sent")
        ; spam = topic (`Name "spam")
        ; trash = topic (`Name "trash")
        ; everything = topic (`Name "everything")
        ; topic = (fun id -> topic (`Id id))
        ; attachments
        }
      ;;
    end

    type t =
      { cable : Uri.t
      ; imbox : Uri.t
      ; topics : Topics.t
      ; invoke : ?verb:Protocol.HTTPS.t -> ?body:string -> Uri.t -> Soup.soup Soup.node
      ; next : Soup.soup Soup.node -> Uri.t option
      }

    let instance = ref None

    let create () =
      { cable = mk ~protocol:Protocol.WSS "/cable"
      ; imbox = mk "/imbox"
      ; topics = Topics.create ()
      ; invoke
      ; next
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
end
