open! Core

module Protocol = struct
  module HTTPS = struct
    type t =
      | DELETE
      | GET
      | POST

    let to_string = function
      | DELETE -> "DELETE"
      | GET -> "GET"
      | POST -> "POST"
    ;;

    let of_string = function
      | "DELETE" -> Some DELETE
      | "GET" -> Some GET
      | "POST" -> Some POST
      | _ -> None
    ;;
  end

  type t =
    | HTTPS
    | IMAP
    | SMTP
    | WSS

  let to_string = function
    | HTTPS -> "https"
    | IMAP -> "imap"
    | SMTP -> "smtp"
    | WSS -> "wss"
  ;;

  let of_uri uri =
    match Uri.scheme uri with
    | Some "https" -> Some HTTPS
    | Some "imap" -> Some IMAP
    | Some "smtp" -> Some SMTP
    | Some "wss" -> Some WSS
    | _ -> None
  ;;

  let of_string str = of_uri (Uri.of_string str)
end
