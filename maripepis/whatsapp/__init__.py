"""La sesión de WhatsApp de Maripepis: un demonio aparte y quien le habla.

Vive en su propio paquete, y no dentro de `tools/`, por una razón que se
descubrió midiendo: la biblioteca que habla el protocolo de WhatsApp
(`neonize`, que por debajo es *whatsmeow*) **se queda con el hilo para siempre**.
Su `connect()` no vuelve nunca, ni cerrando la conexión desde dentro de sus
propios callbacks. No existe el «me conecto, mando el mensaje y cierro»: quien
abre la sesión ya no hace otra cosa.

De ahí sale toda la forma de esto. Un proceso que vive aparte y sostiene la
sesión (`daemon`), un socket unix por el que se le piden cosas (`protocol`), y
una función de tres líneas para pedírselas (`cliente`). Exactamente el mismo
reparto que la tecla de hablar en `maripepis/hotkey/`, y por el mismo motivo:
hay algo caro de arrancar que conviene tener siempre puesto.

Y algo más importante que el hilo: **esto envía de verdad**. La herramienta de
`tools/whatsapp.py` llegaba hasta el borde y paraba, dejando el mensaje escrito
para que le dieras a Enter. Con una sesión propia ya no hay Enter que valga, así
que la red de seguridad se muda aquí y a la confirmación hablada: primero se te
lee a quién va y qué pone, y solo entonces sale.
"""
