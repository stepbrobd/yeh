open! Core
module Protocol = Protocol.Protocol

module Hey (Args : sig
    (* val token : string *)
  end) =
struct
  let domain = "app.hey.com"

  module Endpoint = struct
    let mk ?(protocol = Protocol.HTTPS) domain path =
      Printf.sprintf "%s://%s%s" (Protocol.to_string protocol) domain path
    ;;
  end

  module Api = struct
    let cable = Endpoint.mk ~protocol:Protocol.WSS domain "/cable"
    let imbox = Endpoint.mk domain "/imbox"

    module Topics = struct
      (** [mk t] creates an endpoint for a topic where [t] can be either a named topic or an email id *)
      let mk t = Endpoint.mk domain (Printf.sprintf "/topics/%s" t)

      (** [attachments t] creates an endpoint for all attachments relevant to an email with id [t] *)
      let attachments t = Endpoint.mk domain (Printf.sprintf "/topics/%s/attachments" t)

      let drafts = mk "drafts"
      let sent = mk "sent"
      let spam = mk "spam"
      let trash = mk "trash"
      let everything = mk "everything"
    end
  end
end
