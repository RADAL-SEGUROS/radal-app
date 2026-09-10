# José Francisco Sousa — broker feedback (RAW source, 2026-09-07)

> **Why this doc exists.** José Francisco Sousa is the insurance-broker domain expert on the team.
> This is the **raw, un-paraphrased source** of his feedback on the **Bases Técnicas** expediente and
> the **line / ramo** model — the WhatsApp text thread plus the 7-minute audio he sent (transcribed
> and organised). The implementation this session derived from it (see §16 of
> `docs/technical-reference.md`) turned out to be **wrong in places and must be revisited in the next
> session** — so the primary source is preserved here verbatim to re-derive against. **Treat this as
> the brief; do NOT treat the current build as authoritative over it.**
>
> What it informed: (1) broker-defined **lines/ramos** + recommended **templates**; (2) **"bases
> técnicas = completed antecedentes"** (no middle step); (3) the detailed **PDF formatting** rules.
> Suspected-wrong / open items are tracked in `docs/handoff-2026-09-08.md` and §16.5 of the technical
> reference — the user will confirm exactly which idea to change.

---

## 1. WhatsApp text thread (verbatim)

```
[8:11 PM, 9/7/2026] BenGonzalezAI: dale dale eso es medio riesgoso pero podemos desarrollarlo, igual deberiamos tener plantillas para que ellos usen

[8:11 PM, 9/7/2026] BenGonzalezAI: voy a defininr entonces que ellos deben crear los ramos antes

[8:11 PM, 9/7/2026] BenGonzalezAI: estos ramos se comparten entre grpos?

[8:11 PM, 9/7/2026] BenGonzalezAI: o el ramo inceidio

[8:11 PM, 9/7/2026] BenGonzalezAI: incendio*

[8:12 PM, 9/7/2026] BenGonzalezAI: podría ser distinto en empresa X (agrosuper) que en empresa y (fintual, ni idea)

[8:12 PM, 9/7/2026] BenGonzalezAI: jajaja

[8:13 PM, 9/7/2026] BenGonzalezAI: este es un antecedente listo en el ejemplo que me pasaste. de fuenzalida
ojo podran habe brokers que no suben logos

[8:13 PM, 9/7/2026] BenGonzalezAI: entonces este es un broke que no tiene logo y se le coloca en vez del logo el nombre de la empresa con el estilo pertinente como fallback

[8:15 PM, 9/7/2026] José Francisco Sousa: voy a trabajar una lista de ramos y te la mando, lo más completa posible

[8:16 PM, 9/7/2026] BenGonzalezAI: por ahora lo dejare libre pero para los users test le colocare esas 2 plantillas que enviaste

[8:16 PM, 9/7/2026] BenGonzalezAI: asiq ue si preparala pero no es bloqueante

[8:16 PM, 9/7/2026] José Francisco Sousa: dale

[8:16 PM, 9/7/2026] BenGonzalezAI: la demo tendra 2 plantillas y mas que nada le damos mas poder a broker que algunas veces el aram de doble filo

[8:16 PM, 9/7/2026] José Francisco Sousa: lo ideal es tener una lista desplegable con todos los ramos y seleccione

[8:17 PM, 9/7/2026] BenGonzalezAI: claro pero si pueden cambiar esas serian plantillas

[8:17 PM, 9/7/2026] BenGonzalezAI: le rellenan algunos recomendados

[8:17 PM, 9/7/2026] BenGonzalezAI: pero igual puede eliminar o añadir como quiera

[8:17 PM, 9/7/2026] BenGonzalezAI: este es un antecedente listo en el ejemplo que me pasaste. de fuenzalida
como viste esto?

[8:17 PM, 9/7/2026] BenGonzalezAI: eso seria antecedentes completos basicamente que seria bases tencicas

[8:17 PM, 9/7/2026] BenGonzalezAI: lists para enviar

[8:17 PM, 9/7/2026] BenGonzalezAI: puedo colocar diagramas, cosas mas bonitas lo que quieran

[8:17 PM, 9/7/2026] BenGonzalezAI: pero es el mas simple por temas formales no mas

[8:17 PM, 9/7/2026] José Francisco Sousa: este es un antecedente listo en el ejemplo que me pasaste. de fuenzalida
a grandes rasgos está bien, completo, pero hay que afinar cuestiones de formato

[8:17 PM, 9/7/2026] BenGonzalezAI: eso mismo

[8:18 PM, 9/7/2026] BenGonzalezAI: todo feedback es bienvenido

[8:18 PM, 9/7/2026] BenGonzalezAI: jajajajaj

[8:18 PM, 9/7/2026] BenGonzalezAI: antes que eso wachin

[8:18 PM, 9/7/2026] José Francisco Sousa: voy a mirarlo con detalle y te mando el feedback completo

[8:18 PM, 9/7/2026] BenGonzalezAI: ah nono sigue no mas

[8:18 PM, 9/7/2026] BenGonzalezAI: si pls

[8:18 PM, 9/7/2026] José Francisco Sousa: lo necesitas ahora, para mañana?

[8:27 PM, 9/7/2026] BenGonzalezAI: ahorita

[8:27 PM, 9/7/2026] BenGonzalezAI: que es parte clave del borker view

[8:27 PM, 9/7/2026] BenGonzalezAI: y se me reinician los limites en 1 hora y media

[8:27 PM, 9/7/2026] BenGonzalezAI: jajajajajajajaja

[8:27 PM, 9/7/2026] BenGonzalezAI: entonces ahi vuelvo a ponerle con todo

[8:27 PM, 9/7/2026] BenGonzalezAI: con los ejemplos quem epasaste podríamos tener casi toda la visa del broker completa

[8:27 PM, 9/7/2026] BenGonzalezAI: pero los expedientes son improtante igual si le haremo modificaciones ideal ahora

[8:53 PM, 9/7/2026] José Francisco Sousa: Anda siguiéndolo poco a poco

[8:53 PM, 9/7/2026] José Francisco Sousa: Pero era mejor en audio que escribirlo

[11:02 PM, 9/7/2026] BenGonzalezAI: dale lo proceso no mas

[11:02 PM, 9/7/2026] BenGonzalezAI: pero igual si es mas de 1min ideal lo pases por claude y me mandas puntos claritos que me sirve mas texto igual por temas de trabajar con ia

[11:03 PM, 9/7/2026] BenGonzalezAI: voy a escuharlo en unratito y le pongo
```

---

## 2. The 7-minute audio — feedback on the Bases Técnicas document (transcribed + organised, verbatim)

# Feedback sobre Documento de Seguro - Transcripción Organizada

---

## 1. INTRODUCCIÓN
"Buenas, Benja, ¿cómo va? Oye, prefiero mandarte un audio respecto de este documento. Creo que es más fácil y ahí lo vais siguiendo poco a poco."

---

## 2. SECCIÓN 1 - IDENTIFICACIÓN DEL ASEGURADO
"Bueno, partiendo por la identificación del asegurado, el récord, me parece que está bien. Y entrando ya a los numerales, el punto 1 está perfecto."

---

## 3. SECCIÓN 2 - DIRECCIÓN Y UBICACIONES ASEGURADAS
"Lo único que sí es donde dice dirección, lo dejaría como dirección comercial, ya que abajo en el punto 2 tratamos las ubicaciones. Y ahí, antes de que diga solo ubicaciones, yo le pondría ubicaciones aseguradas."

---

## 4. INTEGRACIÓN PUNTOS 2 Y 4 - TABLA DE MONTOS POR UBICACIÓN Y MATERIA ASEGURADA
"Acá hay un comentario que igual puede ser un poco engorroso, pero que al final hay que mezclar lo que acá dice punto 2, ubicaciones, y materias aseguradas, el punto 4. Al final es mucho mejor mostrar una sola tabla donde se detalla los montos por ubicación y por materia asegurada, que son lo que dice ahí partidas."

**Aclaración sobre terminología:**
"Eso en el lenguaje del seguro no se ocupa como tal, no se ocupa el concepto partidas, se ocupa materias aseguradas."

**Qué incluyen las materias aseguradas:**
"Las materias aseguradas vienen a ser todo lo que son edificio, maquinaria, las cubas en este caso porque una viña, muebles o contenidos en general, existencias, perjuicio por paralización. Todas esas, donde está el punto 4, son materias aseguradas."

**Estructura recomendada de la tabla:**
"Y esas se tienen que aperturar por ubicación asegurada. Por lo tanto, si pensamos en una tabla, las ubicaciones tendrían que estar en la mano izquierda de la tabla, luego la comuna, una breve descripción, si es que la hay, de esa ubicación, y hacia la derecha ir abriendo cada materia asegurada, es decir, edificio. Y ahí uno tira el monto que corresponde a esa ubicación para esa materia asegurada. Y así para maquinaria, existencia, perjuicio por paralización, lo que sea. Y así para cada ubicación."

**Beneficios de este formato:**
"Por lo tanto, tú puedes llegar a tener los totales de edificio o de cualquier materia asegurada para el total cuenta. ¿Vale? Y así llegar al monto total asegurado. Ese es el formato que más acomoda a todos para entender bien cómo se desglosan los montos asegurados por ubicación y materia asegurada."

---

## 5. SECCIÓN 3 - MONTO TOTAL ASEGURADO
"El punto que acá dice 3, materia asegurada, eso corresponde al monto total asegurado, que ya vendría a ser el resultado de esta tabla que te acabo de escribir, juntando punto 2 y 4."

---

## 6. SECCIÓN 5 - COBERTURAS SOLICITADAS (SUBLÍMITES)
"Luego, en lo que acá dice el punto 5, sobre las coberturas solicitadas, hay dos cosas. Por un lado, están los sublímites, que hay que crear una tabla también. Con la denominación o la indicación de la cobertura a mano izquierda y el sublímite propiamente tal que corresponde a esa cobertura a mano derecha de la tabla."

**Problema actual:**
"En este formato no se entiende, la verdad. Es muy difícil de leer y de identificar qué coberturas se están incluyendo y qué sublímite está aplicando. Por lo tanto, por eso se hace en modo tabla."

---

## 7. SECCIÓN 5 - COBERTURAS SOLICITADAS (DEDUCIBLES)
"En la parte final de ese párrafo, que acá está en la página 4, sobre las coberturas solicitadas, están también los deducibles. Y que también hay que aplicar el mismo formato de tabla que acabo de escribir. Es decir, el enunciado del deducible a mano izquierda y el deducible propiamente tal que aplica a mano derecha. ¿Vale?"

**Formato:**
"Y como dos tablas independientes. Por un lado, sublímites, una tabla. Y por otro lado, una tabla aparte y separada, deducibles. ¿Vale?"

---

## 8. PERJUICIO POR PARALIZACIÓN - REORGANIZACIÓN
"En la siguiente página se nombra el perjuicio por paralización, que no sé por qué salió así, pero debe venir dentro de los sublímites, lo que corresponda a sublímite y lo que corresponda a deducibles en deducibles. Aquí, como que se tomó como algo separado y aparte, y no."

**Desglose correcto:**
"En este caso en específico, la cobertura general vendría a ser 10% de la verde indemnizable, mínimo tanto, eso vendría a ser el deducible. Ahí también está el deducible de sismo, perdón, que también debe ir en la tabla de deducibles. Y la última parte dice perjuicio por paralización 10 días de la indemnización diaria, ese es el sublímite, ¿vale? Entonces hay que separarlo en dos y llevar lo que corresponda a la tabla de sublímites y lo que corresponda a la tabla de deducibles."

---

## 9. LÍMITE DE INDEMNIZACIÓN
"El límite de indemnización, acá hay hartas cosas mezcladas. En ese párrafo no corresponde mencionar todo lo que dice ahí sobre el límite de indemnización. Y solamente hasta lo que dice, donde dice el límite de indemnización full value, entre la línea frase 2 y 3, ¿vale?"

---

## 10. VIGENCIA DEL SEGURO
"Además, por ahí vi que dice la vigencia y la comisión del corredor. La vigencia tiene que ir como un título, como un numeral, ¿vale? Y detallarse la vigencia y siempre decir desde las 12 horas del día. Tanto, tanto, a las 12 horas del día, tanto, tanto."

---

## 11. COMISIÓN DEL CORREDOR
"Y la comisión del corredor es la comisión que también se solicita a través de este documento y también debe ir como un numeral aparte antes de la siniestralidad."

---

## 12. SINIESTRALIDAD (PUNTOS 6 Y 7)
"Luego, lo de la siniestralidad está bien, está perfectamente detallado así. Simplemente justaría lo que acá dice el punto 6 y el punto 7, ya que hablan de lo mismo. ¿Cachéis? Entonces, si el 6 dice sí, entonces que se detalle la tabla abajo, pero no es necesario volver a poner 7 siniestros. Dejaría como un solo punto en conjunto."

---

## 13. MEDIDAS DE PROTECCIÓN
"Las medidas de protección están bien. Esto es un breve resumen de lo que viene a hacer el informe de inspección, donde se detallan las medidas de protección propiamente tal. Así que me parece que está bien. Lo único que está todo muy junto y vuelve a pasar un poco lo que pasaba en los párrafos anteriores, como mucha información metida ahí. Creo que se debe hacer como un punteo en este caso o modo tabla con cada medida de protección que se está detallando."

---

## 14. OBSERVACIONES GENERALES
"Y lo mismo también donde dice observaciones generales. Creo que para este caso lo podríamos sacar el punto 9, ya que es harta información que en una de esas acá no es necesario mostrar. Y para el ejemplo, creo que lo podríamos obviar y sacar el punto nueve."

---

## 15. RECOMENDACIÓN FINAL
"Así que eso, o dejar el punto nueve, creo que también pasa lo mismo. Tenemos que hacer como un punteo o una tabla con cada una de las especificaciones que ahí se están mencionando y no meter todo con ese chorizo en un solo documento."

---

## 3. To re-derive next session

- The user has flagged that **the idea we implemented from this conversation is wrong in places** —
  do NOT assume the current §16 build matches José's intent; **re-read this raw source with the user
  and confirm the correction before building.**
- Decisions this conversation established (verify each still holds): brokers **create the ramos/lines
  beforehand**; a ramo (e.g. incendio) **can differ per empresa**; ship **2 recommended templates**
  for the test users (José is preparing a full ramo list — "no es bloqueante"); ideal UX is a
  **dropdown of all ramos** where templates prefill recommended fields but the broker can add/remove;
  **"antecedentes completos = bases técnicas, listo para enviar"**; broker-without-logo → the
  **company name in a styled font** as the logo fallback.
- The PDF format rules (§2 audio) were implemented in §16.4 — re-check them against this source and
  against whatever the user says was wrong.
