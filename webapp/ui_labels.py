"""
Etiquetas em português (pt-PT) para a interface web.

Porquê um módulo separado em vez de traduzir `safety.py`/`service.py`
diretamente: esses valores também são devolvidos pela API REST
(`/api/commands`, `/api/status`, ...), que é consumida por scripts e por
outras máquinas. A API mantém-se em inglês -- estável e previsível para
quem automatiza -- e só o que é mostrado no browser é traduzido aqui.

Os códigos (`"00"`, `"01"`, `"L"`, `"B"`, ...) são os do protocolo PI30 e
nunca mudam; só o texto ao lado é que é traduzido.
"""

from __future__ import annotations

# Prioridade da fonte de carregamento (PCP). Códigos verificados no
# hardware -- ver CLAUDE.md.
PCP_LABELS_PT = {
    "00": "Rede primeiro",
    "01": "Solar primeiro, rede em recurso",
    "02": "Solar e rede",
    "03": "APENAS solar (sem carregar da rede)",
}

# Prioridade da fonte de saída (POP) -- é esta que comuta o relé (o
# "clique" audível), ao contrário do PCP. Ver CLAUDE.md gotcha #7.
POP_LABELS_PT = {
    "00": "Rede primeiro",
    "01": "Solar primeiro",
    "02": "SBU (solar / bateria / rede)",
}

# Modos de funcionamento (resposta ao QMOD). A chave é a letra que o
# aparelho devolve; app.js traduz a partir da letra, não do texto inglês,
# para não depender de uma tradução do lado do servidor.
MODE_LABELS_PT = {
    "P": "Ligado",
    "S": "Em espera",
    "L": "Rede elétrica",
    "B": "Bateria",
    "F": "Avaria",
    "H": "Poupança de energia",
    "Y": "Bypass",
    "D": "Desligado",
    "G": "Modo rede",
}

# Nomes dos campos do QPIRI (valores nominais), tal como aparecem na
# tabela "Configuração" do painel.
RATING_LABELS_PT = {
    "ac_input_voltage": "Tensão de entrada AC",
    "ac_input_current": "Corrente de entrada AC",
    "ac_output_voltage": "Tensão de saída AC",
    "ac_output_frequency": "Frequência de saída AC",
    "ac_output_current": "Corrente de saída AC",
    "ac_output_apparent_power": "Potência aparente de saída",
    "ac_output_active_power": "Potência ativa de saída",
    "battery_voltage": "Tensão da bateria",
    "battery_recharge_voltage": "Tensão de recarga",
    "battery_under_voltage": "Tensão mínima (corte)",
    "battery_bulk_voltage": "Tensão de carga rápida (bulk)",
    "battery_float_voltage": "Tensão de manutenção (float)",
    "battery_type": "Tipo de bateria",
    "max_ac_charging_current": "Corrente máx. de carga pela rede",
    "max_charging_current": "Corrente máx. de carga",
    "input_voltage_range": "Gama de tensão de entrada",
    "output_source_priority": "Prioridade da fonte de saída",
    "charger_source_priority": "Prioridade da fonte de carga",
    "parallel_max_num": "Nº máx. em paralelo",
    "machine_type": "Tipo de aparelho",
    "topology": "Topologia",
    "output_mode": "Modo de saída",
    "battery_redischarge_voltage": "Tensão de nova descarga",
    "pv_ok_condition": "Condição PV OK",
    "pv_power_balance": "Balanço de potência PV",
}

# Tipos de bateria (campo 12 do QPIRI).
BATTERY_TYPE_LABELS_PT = {
    "AGM": "AGM",
    "Flooded": "Chumbo-ácido aberta",
    "User-defined": "Definida pelo utilizador",
}
