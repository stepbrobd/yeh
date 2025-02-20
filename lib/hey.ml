open! Core
module Protocol = Protocol.Protocol

module Hey (Args : sig
    val domain : string
    val token : string
  end) =
struct
  module Api = struct
    let mk ?(protocol = Protocol.HTTPS) path =
      Printf.sprintf "%s://%s%s" (Protocol.to_string protocol) Args.domain path
    ;;

    let invoke uri ?(verb = Protocol.HTTPS.GET) ?(body = "") =
      match Protocol.of_string uri with
      | Some Protocol.HTTPS -> ""
      | Some Protocol.WSS -> ""
      | _ -> failwith "unsupported"
    ;;

    module Topics = struct
      (** [mk t] creates an endpoint for a topic where [t] can be either a named topic or an email id *)
      let topic t = mk (Printf.sprintf "/topics/%s" t)

      (** [attachments t] creates an endpoint for all attachments relevant to an email with id [t] *)
      let attachments t = mk (Printf.sprintf "/topics/%s/attachments" t)

      type t =
        { drafts : string
        ; sent : string
        ; spam : string
        ; trash : string
        ; everything : string
        ; topic : string -> string
        ; attachments : string -> string
        }

      let create () =
        { drafts = topic "drafts"
        ; sent = topic "sent"
        ; spam = topic "spam"
        ; trash = topic "trash"
        ; everything = topic "everything"
        ; topic
        ; attachments
        }
      ;;
    end

    type t =
      { cable : string
      ; imbox : string
      ; topics : Topics.t
      ; invoke : string -> ?verb:Protocol.HTTPS.t -> ?body:string -> string
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
