open! Core

module Smtp = struct
  module Cmd = struct
    type t =
      | Helo of string
      | Ehlo of string
    (* TODO *)
  end
end
