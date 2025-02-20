open! Core

module Protocol = struct
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
end
