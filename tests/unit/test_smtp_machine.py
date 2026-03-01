from yeh.smtp import Machine, NewMail, ReplyMail


def test_smtp_machine_parses_recipients_from_rcpt_commands() -> None:
    machine = Machine()
    assert machine.handle("EHLO localhost")[0] == 250
    assert machine.handle("MAIL FROM:<alice@example.com>")[0] == 250
    assert machine.handle("RCPT TO:<Bob <bob@example.com>>")[0] == 250
    assert machine.handle("DATA")[0] == 354
    machine.handle("To: Bob <bob@example.com>")
    machine.handle("Cc: Carol <carol@example.com>")
    machine.handle("Subject: hi")
    machine.handle("")
    machine.handle("hello")
    code, _, action = machine.handle(".")
    assert code == 250
    assert isinstance(action, NewMail)
    assert action.to == ("bob@example.com",)
    assert action.cc == ()
    assert action.bcc == ()


def test_smtp_machine_reply_action_when_header_present() -> None:
    machine = Machine()
    machine.handle("EHLO localhost")
    machine.handle("MAIL FROM:<alice@example.com>")
    machine.handle("RCPT TO:<bob@example.com>")
    machine.handle("RCPT TO:<cc@example.com>")
    machine.handle("DATA")
    machine.handle("To: Bob <bob@example.com>")
    machine.handle("Cc: C C <cc@example.com>")
    machine.handle("X-HEY-Reply-Entry-ID: 42")
    machine.handle("Subject: reply")
    machine.handle("")
    machine.handle("hello")
    _, _, action = machine.handle(".")
    assert isinstance(action, ReplyMail)
    assert action.entry_id == "42"
    assert action.to == ("bob@example.com",)
    assert action.cc == ("cc@example.com",)
