from maripepis.llm.conversation import Conversation


def test_messages_empiezan_por_user_tras_recorte():
    conv = Conversation("sys", max_history=2)
    conv.add_user("u1")
    conv.add_assistant("a1")
    conv.add_user("u2")  # turno actual (aún sin respuesta)
    # Últimos 2 = [a1, u2] → se descarta el asistente inicial → [u2]
    assert conv.messages == [{"role": "user", "content": "u2"}]


def test_alternancia_y_primer_turno_user():
    conv = Conversation("sys", max_history=10)
    for i in range(3):
        conv.add_user(f"u{i}")
        conv.add_assistant(f"a{i}")
    conv.add_user("u_actual")
    msgs = conv.messages
    assert msgs[0]["role"] == "user"
    roles = [m["role"] for m in msgs]
    # alternancia estricta
    assert all(roles[i] != roles[i + 1] for i in range(len(roles) - 1))


def test_undo_last_user():
    conv = Conversation("sys")
    conv.add_user("hola")
    conv.undo_last_user()
    assert conv.messages == []
    # undo es seguro aunque no haya turno de usuario al final
    conv.add_user("u")
    conv.add_assistant("a")
    conv.undo_last_user()  # el último es assistant → no hace nada
    assert conv.messages[-1]["role"] == "assistant"


def test_reset_vacia_el_historial():
    conv = Conversation("sys")
    conv.add_user("hola")
    conv.add_assistant("qué tal")
    conv.reset()
    assert conv.messages == []
    # el system prompt se mantiene: es la misma Maripepis, otra conversación
    assert conv.system_prompt == "sys"
