---
titulo: Taxa ao mês e taxa ao ano não se convertem multiplicando por 12
gatilhos: [parcelado, juros]
---
Ideia central: como os juros compõem, 1% ao mês equivale a (1,01)^12 - 1 = 12,68% ao ano, e não 12%. No sentido inverso, 15% ao ano equivalem a (1,15)^(1/12) - 1 = 1,17% ao mês.

Por que importa: lojas e bancos anunciam a taxa na unidade que parece menor. "Só 2,99% ao mês" é 42,4% ao ano. Para comparar duas opções, coloque as duas na mesma unidade e sempre pelo cálculo composto.

Como usar: o sistema converte o CDI anual para mensal antes de comparar com qualquer parcelamento (função annual_to_monthly). Quando um vendedor falar em taxa, a pergunta certa é "qual o custo efetivo total (CET) ao ano?". O CET inclui IOF e tarifas, por isso costuma ser maior que a taxa anunciada.

Pergunta para pensar: um cartão que cobra 12% ao mês no rotativo cobra quanto ao ano? Faça a conta com a fórmula acima antes de olhar a resposta (passa de 280%).
