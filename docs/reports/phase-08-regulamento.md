# Phase 08

Status: PASS

## Implementado
`aurora/regulation.py`:
- índice de capítulos e artigos;
- termos normalizados com sinônimos;
- pontuação com peso extra para o título do capítulo;
- retorno de até 3 artigos de **um único capítulo**;
- limiar mínimo: assunto inexistente devolve `encontrado: false`.

## Arquivos alterados
`aurora/regulation.py`, `aurora/tools.py`, `aurora/agents.py`

## Testes executados
`test_pool_sunday_only_pool_chapter` e `test_unknown_subject_returns_nothing`. O passo 12 do validador real
verifica que nenhum parágrafo de outro capítulo aparece em qualquer string dos eventos.

## Resultado
"Até que horas a piscina funciona aos domingos?" → Capítulo IV, Art. 22 ("Aos domingos e feriados,
a piscina funciona das 9h às 20h"). Resposta real do Gemini: "...aos domingos e feriados a piscina funciona
das **9h às 20h**, conforme estabelecido no **Artigo 22, inciso II**."

## Problemas encontrados
- "cachorro" não encontrava o capítulo de animais.
- Perguntas sem relação com o regulamento devolviam um capítulo qualquer.
- O `regulation_agent` usava uma pergunta antiga da sessão.

## Correções realizadas
Sinônimos coloquiais, `MIN_SCORE` e `include_contents="none"`.

## Evidências
`12 PASS — 20h=True leaked=0 tool_calls=17 eventos=68`.

## Pendências
Nenhuma.
