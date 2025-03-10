open! Core

module Config = struct
  let prefix = "YEH"

  module Mail (Args : sig
      val prefix : string
      val default_host : string
      val default_host_env : string
      val default_port : int
      val default_port_env : string
    end) =
  struct
    type t =
      { host : string
      ; port : int
      }

    let default = { host = Args.default_host; port = Args.default_port }

    let of_env () =
      let host =
        Sys.getenv (Args.prefix ^ Args.default_host_env)
        |> Option.value ~default:default.host
      in
      let port =
        Sys.getenv (Args.prefix ^ Args.default_port_env)
        |> Option.map ~f:Int.of_string
        |> Option.value ~default:default.port
      in
      { host; port }
    ;;
  end

  module Imap = Mail (struct
      let prefix = prefix ^ "_IMAP"
      let default_host = "127.0.0.1"
      let default_host_env = "_HOST"
      let default_port = 993
      let default_port_env = "_PORT"
    end)

  module Smtp = Mail (struct
      let prefix = prefix ^ "_SMTP"
      let default_host = "127.0.0.1"
      let default_host_env = "_HOST"
      let default_port = 465
      let default_port_env = "_PORT"
    end)

  type t =
    { domain : string
    ; user : string
    ; pass : string
    ; asid : int
    ; csrf : string
    ; cookie : string
    ; imap : Imap.t
    ; smtp : Smtp.t
    }

  let instance = ref None

  let of_env () =
    { domain = Sys.getenv_exn (prefix ^ "_DOMAIN")
    ; user = Sys.getenv_exn (prefix ^ "_USER")
    ; pass = Sys.getenv_exn (prefix ^ "_PASS")
    ; asid = Sys.getenv_exn (prefix ^ "_ASID") |> Int.of_string
    ; csrf = Sys.getenv_exn (prefix ^ "_CSRF")
    ; cookie = Sys.getenv_exn (prefix ^ "_COOKIE")
    ; imap = Imap.of_env ()
    ; smtp = Smtp.of_env ()
    }
  ;;

  let init () =
    match !instance with
    | Some _ -> ()
    | None -> instance := Some (of_env ())
  ;;

  let instance () =
    match !instance with
    | Some config -> config
    | None ->
      init ();
      Option.value_exn !instance
  ;;
end
