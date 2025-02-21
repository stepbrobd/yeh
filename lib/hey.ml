open! Core
module Protocol = Protocol.Protocol

module Hey (Args : sig
    val domain : string
    val token : string
  end) =
struct
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
      }

    let instance = ref None

    let create () =
      { cable = mk ~protocol:Protocol.WSS "/cable"
      ; imbox = mk "/imbox"
      ; topics = Topics.create ()
      ; invoke
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
