from yeh import routes


def test_parse_mailbox_friendly_supports_labels_and_inbox_alias() -> None:
    assert routes.parse_mailbox_friendly("Inbox") == routes.Mailbox.IMBOX
    assert (
        routes.parse_mailbox_friendly("Previously Seen")
        == routes.Mailbox.PREVIOUSLY_SEEN
    )
    assert routes.parse_mailbox_friendly("screened_out") == routes.Mailbox.SCREENED_OUT


def test_mailbox_url_and_text_url_are_https() -> None:
    url = routes.mailbox_url(routes.HOST, routes.Mailbox.SPAM)
    assert url == "https://app.hey.com/topics/spam"
    text_url = routes.message_text_url(routes.HOST, "123")
    assert text_url == "https://app.hey.com/messages/123.text"
