open! Core

module Imap = struct
  module Cmd = struct
    type t =
      | Login of string * string
      | Logout
      | Select of string
    (* TODO *)
  end
end
