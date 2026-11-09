"use strict";

(() => {
  const byId = (id) => document.getElementById(id);
  const views = ["produtos", "clientes", "documentos", "pedidos"];
  const state = {
    token: "",
    generation: 0,
    view: "produtos",
    selectedDocument: null,
    selectedOrder: null,
    pages: Object.fromEntries(
      views.map((view) => [
        view,
        { offset: 0, limit: 20, query: "", revisao: false },
      ]),
    ),
    requests: new Map(),
  };
  const currency = new Intl.NumberFormat("pt-BR", {
    style: "currency",
    currency: "BRL",
  });
  const dates = new Intl.DateTimeFormat("pt-BR", {
    dateStyle: "short",
    timeStyle: "short",
  });
  const money = (value) =>
    value == null ? "Sem preço" : currency.format(Number(value));
  const date = (value) =>
    value && !Number.isNaN(Date.parse(value))
      ? dates.format(new Date(value))
      : "—";
  const shortId = (value) => String(value || "").slice(0, 8);
  const reviewReasons = {
    texto_nao_verificavel:
      "Documento escaneado ou imagem: o texto não pôde ser conferido automaticamente",
    casamento_por_descricao: "Itens identificados apenas pela descrição",
    possivel_injecao: "O documento contém instruções direcionadas ao sistema",
    extracao_suspeita: "Extração incompleta ou inconsistente",
    total_acima_limite: "Total acima do limite de revisão",
    quantidade_acima_limite: "Quantidade acima do limite de revisão",
    decisao_rebaixada: "Uma verificação automática alterou a decisão do agente",
  };
  const decisions = {
    criar_rascunho: "Rascunho criado",
    pedir_esclarecimento: "Esclarecimento necessário",
    rejeitar: "Pedido rejeitado",
    falha: "Falha no processamento",
  };
  const fileTypes = {
    "application/pdf": ["PDF", "Documento PDF"],
    "text/plain": ["TXT", "Documento de texto"],
    "image/png": ["PNG", "Imagem PNG"],
    "image/jpeg": ["JPEG", "Imagem JPEG"],
  };
  const fileType = (type, long = false) =>
    (fileTypes[type] || ["Arquivo", "Arquivo"])[long ? 1 : 0];
  const reasonText = (code) => reviewReasons[code] || code;
  const size = (bytes) =>
    bytes >= 1024 * 1024
      ? `${(bytes / 1024 / 1024).toFixed(1)} MB`
      : `${Math.max(1, Math.ceil(bytes / 1024))} KB`;

  function node(tag, text, className) {
    const element = document.createElement(tag);
    if (text != null) element.textContent = text;
    if (className) element.className = className;
    return element;
  }

  function notice(message, error = false) {
    const element = byId("notice");
    element.textContent = message;
    element.className = `notice${error ? " error" : ""}`;
    element.hidden = !message;
    element.setAttribute("role", error ? "alert" : "status");
    element.setAttribute("aria-live", error ? "assertive" : "polite");
  }

  function logout(message = "") {
    state.generation += 1;
    state.requests.forEach((controller) => controller.abort());
    state.requests.clear();
    state.token = "";
    state.selectedDocument = state.selectedOrder = null;
    byId("api-token").value = "";
    byId("document-file").value = "";
    byId("document-file").disabled = false;
    byId("workspace").hidden = true;
    byId("login-panel").hidden = false;
    byId("logout").hidden = true;
    byId("upload-button").disabled = false;
    byId("upload-button").textContent = "Enviar documento ↑";
    for (const id of ["refresh-documents", "refresh-orders"]) {
      byId(id).disabled = false;
      byId(id).textContent = "Atualizar";
    }
    for (const view of views) {
      state.pages[view] = { offset: 0, limit: 20, query: "", revisao: false };
      if (byId(`${view}-revisao`)) byId(`${view}-revisao`).checked = false;
      byId(`${view}-body`).replaceChildren();
      byId(`${view}-pagination`).replaceChildren();
      byId(`view-${view}`).removeAttribute("aria-busy");
      if (byId(`${view}-query`)) byId(`${view}-query`).value = "";
      if (byId(`${view}-count`))
        byId(`${view}-count`).textContent = `— ${view}`;
    }
    openView("produtos", false);
    for (const id of ["document-detail", "order-detail"]) {
      byId(id).replaceChildren();
      byId(id).hidden = true;
    }
    notice(message, Boolean(message));
    byId("api-token").focus();
  }

  async function api(path, options = {}, channel = path) {
    if (!state.token) throw new DOMException("Sessão encerrada", "AbortError");
    const generation = state.generation;
    state.requests.get(channel)?.abort();
    const controller = new AbortController();
    state.requests.set(channel, controller);
    const ensureCurrent = () => {
      if (
        controller.signal.aborted ||
        generation !== state.generation ||
        !state.token
      ) {
        throw new DOMException("Requisição cancelada", "AbortError");
      }
    };
    try {
      const response = await fetch(path, {
        ...options,
        signal: controller.signal,
        headers: { ...options.headers, Authorization: `Bearer ${state.token}` },
      });
      ensureCurrent();
      if (response.status === 401) {
        logout(
          "Chave de acesso inválida ou expirada. Confira sua chave e entre novamente.",
        );
        throw new DOMException("Sessão encerrada", "AbortError");
      }
      if (!response.ok) {
        let message = `Não foi possível concluir a operação (HTTP ${response.status}).`;
        try {
          const body = await response.json();
          if (typeof body.mensagem === "string") message = body.mensagem;
          if (Array.isArray(body.problemas) && body.problemas.length)
            message += ` ${body.problemas.map((p) => p.mensagem).join(" ")}`;
          else if (typeof body.detail === "string") message = body.detail;
          else if (Array.isArray(body.detail))
            message = body.detail.map((item) => item.msg).join("; ");
        } catch {
          /* Preserve the status-based message for non-JSON errors. */
        }
        ensureCurrent();
        throw new Error(message);
      }
      const result = options.binary
        ? await response.blob()
        : await response.json();
      ensureCurrent();
      return result;
    } catch (error) {
      ensureCurrent();
      if (error.name === "TypeError")
        throw new Error(
          "Não foi possível conectar ao serviço. Confira sua conexão e tente novamente.",
        );
      throw error;
    } finally {
      if (state.requests.get(channel) === controller)
        state.requests.delete(channel);
    }
  }

  function report(error) {
    if (error.name !== "AbortError")
      notice(error.message || "Ocorreu um erro inesperado.", true);
  }

  function cell(row, value, className) {
    const td = node("td", null, className);
    td.append(
      value instanceof Node ? value : document.createTextNode(value ?? "—"),
    );
    row.append(td);
    return td;
  }

  function titleCell(title, subtitle, mono = false) {
    const content = node("div");
    content.append(node("span", title, `cell-title${mono ? " mono" : ""}`));
    if (subtitle) content.append(node("span", subtitle, "cell-subtitle"));
    return content;
  }

  function badge(label, style = "muted") {
    return node("span", label, `badge ${style}`);
  }

  function reviewBadge(item) {
    if (!item.revisao_obrigatoria) return null;
    const element = badge("Revisão obrigatória", "review");
    const reasons = (item.motivos_revisao || []).map(reasonText);
    if (reasons.length) element.title = reasons.join("; ");
    return element;
  }

  function withReview(content, item) {
    const review = reviewBadge(item);
    if (!review) return content;
    const wrapper = node("div");
    wrapper.append(content, review);
    return wrapper;
  }

  function reviewStrip(item) {
    if (!item.revisao_obrigatoria) return null;
    const strip = node("div", null, "review-strip");
    strip.append(node("strong", "Revisão obrigatória. "));
    const reasons = (item.motivos_revisao || []).map(reasonText);
    strip.append(
      document.createTextNode(
        reasons.length
          ? `${reasons.join("; ")}.`
          : "Confira os itens com atenção antes de usar este rascunho.",
      ),
    );
    return strip;
  }

  function button(label, callback, className = "button small secondary") {
    const element = node("button", label, className);
    element.type = "button";
    element.addEventListener("click", callback);
    return element;
  }

  function emptyRow(body, columns, message) {
    const row = node("tr");
    const td = cell(row, message, "table-empty");
    td.colSpan = columns;
    body.replaceChildren(row);
  }

  function paginate(view, data) {
    const { offset, limit } = state.pages[view];
    const root = byId(`${view}-pagination`);
    const total = Number(data.total || 0);
    const count = data.items.length;
    root.replaceChildren(
      node(
        "span",
        total
          ? `${offset + 1}–${offset + count} de ${total}`
          : "Nenhum registro",
        "pagination-label",
      ),
    );
    const actions = node("div", null, "pagination-actions");
    const previous = button("← Anterior", () => {
      state.pages[view].offset = Math.max(0, offset - limit);
      loadPage(view);
    });
    const next = button("Próxima →", () => {
      state.pages[view].offset = offset + limit;
      loadPage(view);
    });
    previous.disabled = offset === 0;
    next.disabled = offset + count >= total;
    actions.append(previous, next);
    root.append(actions);
  }

  const renderers = {
    produtos(item) {
      const row = node("tr");
      cell(row, titleCell(item.nome, `${item.marca} · ${item.categoria}`));
      cell(row, titleCell(item.ean, item.sku, true));
      cell(row, item.embalagem);
      cell(row, money(item.preco_unitario), "numeric");
      cell(
        row,
        `${item.estoque_disponivel} ${item.unidade}`,
        `numeric${item.estoque_disponivel === 0 ? " stock-empty" : ""}`,
      );
      cell(
        row,
        badge(
          item.ativo ? "Ativo" : "Inativo",
          item.ativo ? "success" : "muted",
        ),
      );
      return row;
    },
    clientes(item) {
      const row = node("tr");
      cell(row, titleCell(item.nome));
      cell(row, item.codigo, "mono");
      cell(row, item.email || "—");
      cell(
        row,
        badge(
          item.ativo ? "Ativo" : "Inativo",
          item.ativo ? "success" : "muted",
        ),
      );
      return row;
    },
    documentos(item) {
      const row = node("tr");
      cell(
        row,
        titleCell(item.nome_original, fileType(item.content_type, true)),
      );
      cell(row, date(item.criado_em));
      cell(row, size(item.tamanho_bytes));
      cell(row, withReview(documentBadge(item.status), item));
      const open = button("Ver documento", () =>
        showDocument(item.documento_id),
      );
      open.setAttribute("aria-label", `Ver documento ${item.nome_original}`);
      cell(row, open);
      return row;
    },
    pedidos(item) {
      const row = node("tr");
      cell(
        row,
        withReview(
          titleCell(`#${shortId(item.pedido_id)}`, "Rascunho", true),
          item,
        ),
      );
      cell(row, date(item.criado_em));
      cell(
        row,
        titleCell(
          item.cliente_nome || item.cliente_codigo || "Cliente cadastrado",
          item.referencia_documento || "Sem referência",
        ),
      );
      cell(row, money(item.total), "numeric");
      const open = button("Ver pedido", () => showOrder(item.pedido_id));
      open.setAttribute("aria-label", `Ver pedido ${shortId(item.pedido_id)}`);
      cell(row, open);
      return row;
    },
  };

  function documentBadge(status) {
    const labels = {
      pedido_criado: ["Pedido criado", "success"],
      aguardando_esclarecimento: ["Aguardando esclarecimento", "waiting"],
      rejeitado: ["Rejeitado", "danger"],
      falha_processamento: ["Falha no processamento", "danger"],
    };
    const [label, style] = labels[status] || [
      "Aguardando processamento",
      "waiting",
    ];
    return badge(label, style);
  }

  async function loadPage(view, preloaded) {
    if (!state.token) return;
    const section = byId(`view-${view}`);
    section.setAttribute("aria-busy", "true");
    const body = byId(`${view}-body`);
    const columns = { produtos: 6, clientes: 4, documentos: 5, pedidos: 5 };
    emptyRow(body, columns[view], "Carregando registros…");
    byId(`${view}-pagination`).replaceChildren();
    try {
      const page = state.pages[view];
      const query = new URLSearchParams({
        limit: page.limit,
        offset: page.offset,
      });
      if (page.query) query.set("consulta", page.query);
      if (page.revisao) query.set("revisao", "true");
      const data =
        preloaded || (await api(`/api/${view}?${query}`, {}, `list-${view}`));
      if (!state.token) return;
      if (data.items.length === 0 && data.total > 0 && page.offset > 0) {
        page.offset = 0;
        return loadPage(view);
      }
      const messages = {
        produtos: "Nenhum produto encontrado. Tente outro nome, SKU ou EAN.",
        clientes: "Nenhum cliente encontrado. Confira o nome ou código.",
        documentos: page.revisao
          ? "Nenhum documento com revisão obrigatória."
          : "Nenhum documento enviado. Selecione um PDF, TXT ou imagem acima para começar.",
        pedidos: page.revisao
          ? "Nenhum pedido com revisão obrigatória."
          : "Nenhum pedido criado. Os pedidos disponíveis aparecerão nesta seção.",
      };
      body.replaceChildren(...data.items.map(renderers[view]));
      if (!data.items.length) emptyRow(body, columns[view], messages[view]);
      paginate(view, data);
      if (byId(`${view}-count`)) {
        const label =
          data.total === 1
            ? { produtos: "produto", clientes: "cliente" }[view]
            : view;
        byId(`${view}-count`).textContent = `${data.total} ${label}`;
      }
    } catch (error) {
      if (error.name !== "AbortError") {
        emptyRow(
          body,
          columns[view],
          "Não foi possível carregar os registros. Tente atualizar a seção.",
        );
        report(error);
      }
    } finally {
      if (!state.requests.has(`list-${view}`))
        section.removeAttribute("aria-busy");
    }
  }

  function openView(view, load = true) {
    if (!views.includes(view)) return;
    state.view = view;
    document.querySelectorAll("[data-view]").forEach((element) => {
      const active = element.dataset.view === view;
      element.classList.toggle("active", active);
      if (active) element.setAttribute("aria-current", "page");
      else element.removeAttribute("aria-current");
    });
    for (const name of views) byId(`view-${name}`).hidden = name !== view;
    if (load) {
      notice("");
      loadPage(view);
    }
  }

  function metadata(entries) {
    const list = node("dl", null, "metadata");
    for (const [label, value] of entries) {
      const group = node("div");
      group.append(node("dt", label), node("dd", value || "—"));
      list.append(group);
    }
    return list;
  }

  function technicalDetails(entries) {
    const details = node("details", null, "technical-details");
    details.append(node("summary", "Detalhes técnicos"), metadata(entries));
    return details;
  }

  async function downloadDocument(item, control) {
    control.disabled = true;
    try {
      const blob = await api(
        `/api/documentos/${encodeURIComponent(item.documento_id)}/arquivo`,
        { binary: true },
        "download",
      );
      const url = URL.createObjectURL(blob);
      const link = node("a");
      link.href = url;
      link.download = item.nome_original;
      document.body.append(link);
      link.click();
      link.remove();
      setTimeout(() => URL.revokeObjectURL(url), 30000);
    } catch (error) {
      report(error);
    } finally {
      control.disabled = false;
    }
  }

  function analysisPanel(analysis) {
    const panel = node("div", null, "analysis-panel");
    panel.append(node("h4", "Resultado do processamento"));
    if (!analysis) {
      panel.append(
        node(
          "p",
          "Este documento ainda não foi processado. Atualize esta seção para conferir novos resultados.",
          "empty-hint",
        ),
      );
      return panel;
    }
    panel.append(
      badge(
        decisions[analysis.decisao] || analysis.decisao,
        analysis.decisao === "criar_rascunho"
          ? "success"
          : analysis.decisao === "pedir_esclarecimento"
            ? "waiting"
            : "danger",
      ),
    );
    if (analysis.perguntas?.length) {
      panel.append(node("p", "Perguntas para o cliente:"));
      const list = node("ul");
      for (const question of analysis.perguntas)
        list.append(node("li", question));
      panel.append(list);
    }
    if (analysis.motivos?.length) {
      panel.append(node("p", "Motivos registrados:"));
      const list = node("ul");
      for (const reason of analysis.motivos)
        list.append(node("li", reason.mensagem));
      panel.append(list);
    }
    panel.append(
      node(
        "p",
        analysis.modelo === "humano"
          ? `Resolvido na interface em ${date(analysis.criado_em)}`
          : `Processado em ${date(analysis.criado_em)} · modelo ${analysis.modelo} · versão ${analysis.versao_prompt}`,
        "help",
      ),
    );
    return panel;
  }

  const productLabel = (p) =>
    `${p.nome} — ${p.marca} · ${p.embalagem} · ${money(p.preco_unitario)} · estoque ${p.estoque_disponivel}`;
  const clientLabel = (c) => `${c.codigo} — ${c.nome}`;

  function fillSelect(select, options, label, selected) {
    const current = selected ?? select.value;
    select.replaceChildren(node("option", "Selecione…"));
    select.firstChild.value = "";
    for (const option of options) {
      const element = node("option", label(option));
      element.value = option.produto_id || option.cliente_id;
      select.append(element);
    }
    if ([...select.options].some((o) => o.value === current))
      select.value = current;
  }

  function searchControl(id, placeholder, onSearch) {
    const wrapper = node("div", null, "resolution-search");
    const input = node("input");
    input.type = "search";
    input.id = id;
    input.placeholder = placeholder;
    input.setAttribute("aria-label", placeholder);
    input.maxLength = 200;
    const go = button("Buscar", async () => {
      const query = input.value.trim();
      if (query.length < 2) return notice("Digite ao menos 2 caracteres.", true);
      go.disabled = true;
      try {
        await onSearch(query);
      } catch (error) {
        report(error);
      } finally {
        go.disabled = false;
      }
    });
    input.addEventListener("keydown", (event) => {
      if (event.key === "Enter") {
        event.preventDefault();
        go.click();
      }
    });
    wrapper.append(input, go);
    return wrapper;
  }

  function resolutionForm(documentItem, analysis) {
    const pend = analysis.pendencias;
    const form = node("form", null, "resolution-form");
    form.append(
      node("h4", "Resolver esclarecimento"),
      node(
        "p",
        "Escolha cliente, produtos e quantidades conforme a resposta do cliente. O rascunho passa pelas mesmas validações de preço e estoque e não confirma a compra.",
        "help",
      ),
    );

    // Cliente
    const clientBox = node("fieldset", null, "resolution-item");
    clientBox.append(node("legend", "Cliente"));
    const doc = [pend.cliente.codigo_documento, pend.cliente.nome_documento]
      .filter(Boolean)
      .join(" — ");
    clientBox.append(node("p", `No documento: ${doc || "não informado"}`, "help"));
    if (pend.cliente.pergunta)
      clientBox.append(node("p", pend.cliente.pergunta, "resolution-question"));
    const clientSelect = node("select");
    clientSelect.id = "res-cliente";
    clientSelect.required = true;
    const clientLabelEl = node("label", "Cliente do pedido");
    clientLabelEl.htmlFor = clientSelect.id;
    const docCode = (pend.cliente.codigo_documento || "").toUpperCase();
    const preselected =
      pend.cliente.cliente_id ||
      pend.cliente.opcoes.find((c) => c.codigo.toUpperCase() === docCode)
        ?.cliente_id;
    fillSelect(clientSelect, pend.cliente.opcoes, clientLabel, preselected);
    clientBox.append(
      clientLabelEl,
      clientSelect,
      searchControl("res-cliente-busca", "Buscar cliente por nome ou código", async (q) => {
        const data = await api(
          `/api/clientes?${new URLSearchParams({ consulta: q, limit: 10 })}`,
          {},
          "res-cliente",
        );
        fillSelect(clientSelect, data.items.filter((c) => c.ativo), clientLabel);
      }),
    );
    form.append(clientBox);

    // Itens
    const rows = [];
    for (const item of pend.itens) {
      const box = node("fieldset", null, "resolution-item");
      const n = item.item_idx + 1;
      box.append(node("legend", `Item ${n}: ${item.descricao}`));
      const asked = [item.quantidade_texto, item.unidade_texto].filter(Boolean).join(" ");
      box.append(
        node("p", `Pedido no documento: ${asked || "quantidade não informada"}`, "help"),
        badge(
          item.situacao === "resolvido" ? "Resolvido pelo agente" : "Pendente",
          item.situacao === "resolvido" ? "success" : "waiting",
        ),
      );
      if (item.pergunta) box.append(node("p", item.pergunta, "resolution-question"));
      const include = node("input");
      include.type = "checkbox";
      include.checked = true;
      include.id = `res-incluir-${n}`;
      const includeLabel = node("label", null, "resolution-check");
      includeLabel.append(include, document.createTextNode(" Incluir no rascunho"));
      const select = node("select");
      select.id = `res-produto-${n}`;
      const selectLabel = node("label", "Produto");
      selectLabel.htmlFor = select.id;
      const ambiguous = String(item.codigo || "").startsWith("AMBIGUIDADE");
      const single = item.opcoes.length === 1 && !ambiguous;
      fillSelect(
        select,
        item.opcoes,
        productLabel,
        item.produto_id || (single ? item.opcoes[0].produto_id : ""),
      );
      const quantity = node("input");
      quantity.type = "number";
      quantity.min = "1";
      quantity.max = "100000";
      quantity.step = "1";
      quantity.id = `res-quantidade-${n}`;
      if (item.quantidade) quantity.value = item.quantidade;
      const quantityLabel = node("label", "Quantidade (embalagens inteiras)");
      quantityLabel.htmlFor = quantity.id;
      include.addEventListener("change", () => {
        select.disabled = quantity.disabled = !include.checked;
      });
      box.append(
        includeLabel,
        selectLabel,
        select,
        searchControl(`res-busca-${n}`, "Buscar outro produto no catálogo", async (q) => {
          const data = await api(
            `/api/produtos?${new URLSearchParams({ consulta: q, limit: 10 })}`,
            {},
            `res-produto-${n}`,
          );
          fillSelect(select, data.items.filter((p) => p.ativo), productLabel);
        }),
        quantityLabel,
        quantity,
      );
      rows.push({ item, include, select, quantity });
      form.append(box);
    }

    const note = node("textarea");
    note.id = "res-observacao";
    note.maxLength = 500;
    note.rows = 2;
    const noteLabel = node("label", "Observação (opcional, ex.: resposta do cliente)");
    noteLabel.htmlFor = note.id;
    const submit = node("button", "Criar rascunho com esta resolução", "button primary");
    submit.type = "submit";
    form.append(noteLabel, note, submit);

    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      const itens = [];
      for (const row of rows) {
        if (!row.include.checked) continue;
        const qty = Number(row.quantity.value);
        if (!row.select.value)
          return notice(`Escolha o produto do item ${row.item.item_idx + 1}.`, true);
        if (!Number.isInteger(qty) || qty < 1 || qty > 100000)
          return notice(
            `Informe uma quantidade inteira entre 1 e 100.000 no item ${row.item.item_idx + 1}.`,
            true,
          );
        itens.push({
          item_idx: row.item.item_idx,
          produto_id: row.select.value,
          quantidade: qty,
        });
      }
      if (!clientSelect.value) return notice("Escolha o cliente do pedido.", true);
      if (!itens.length) return notice("Inclua ao menos um item no rascunho.", true);
      submit.disabled = true;
      submit.textContent = "Criando rascunho…";
      notice("");
      try {
        await api(
          `/api/documentos/${encodeURIComponent(documentItem.documento_id)}/resolucao`,
          {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              analise_id: analysis.analise_id,
              cliente_id: clientSelect.value,
              itens,
              observacao: note.value.trim() || null,
            }),
          },
          "resolucao",
        );
        notice("Rascunho criado a partir da sua resolução.");
        await Promise.all([
          loadPage("documentos"),
          showDocument(documentItem.documento_id, false),
        ]);
      } catch (error) {
        report(error);
        submit.disabled = false;
        submit.textContent = "Criar rascunho com esta resolução";
      }
    });
    return form;
  }

  async function showDocument(id, scroll = true) {
    if (!state.token) return;
    state.selectedDocument = id;
    const root = byId("document-detail");
    root.hidden = false;
    root.replaceChildren(node("p", "Carregando documento…", "help"));
    try {
      const item = await api(
        `/api/documentos/${encodeURIComponent(id)}`,
        {},
        "document-detail",
      );
      const heading = node("div", null, "detail-heading");
      const title = node("div");
      title.append(
        node("h3", item.nome_original),
        node(
          "p",
          item.status === "aguardando_agente"
            ? "Arquivo recebido e disponível para processamento."
            : "Arquivo processado. Confira o resultado abaixo.",
        ),
      );
      const download = button("Baixar arquivo", () =>
        downloadDocument(item, download),
      );
      heading.append(title, download);
      const related = node("div", null, "related-orders");
      related.append(node("h4", "Pedidos vinculados"));
      if (!item.pedidos?.length) {
        related.append(
          node(
            "p",
            "Ainda não há pedidos vinculados a este documento. Atualize esta seção para conferir novos resultados.",
            "empty-hint",
          ),
        );
      } else {
        for (const order of item.pedidos) {
          const line = node("div", null, "related-order");
          line.append(
            titleCell(
              `Pedido #${shortId(order.pedido_id)}`,
              `${date(order.criado_em)} · ${money(order.total)}`,
            ),
          );
          line.append(
            button("Conferir pedido →", () => {
              openView("pedidos");
              showOrder(order.pedido_id);
            }),
          );
          related.append(line);
        }
      }
      const latest = item.analises?.[0];
      const children = [
        heading,
        documentBadge(item.status),
        reviewStrip({
          revisao_obrigatoria: item.revisao_obrigatoria,
          motivos_revisao: latest?.motivos_revisao,
        }),
        node("div", null, "detail-spacer"),
        metadata([
          ["Enviado em", date(item.criado_em)],
          [
            "Formato e tamanho",
            `${fileType(item.content_type)} · ${size(item.tamanho_bytes)}`,
          ],
        ]),
        analysisPanel(latest),
        item.status === "aguardando_esclarecimento" && latest?.pendencias
          ? resolutionForm(item, latest)
          : item.status === "aguardando_esclarecimento"
            ? node(
                "p",
                "Este esclarecimento não tem opções estruturadas; obtenha a resposta do cliente e envie um documento corrigido.",
                "empty-hint",
              )
            : null,
        related,
        technicalDetails([
          ["Identificador do documento", item.documento_id],
          ["SHA-256", item.sha256],
        ]),
      ];
      root.replaceChildren(...children.filter(Boolean));
      if (scroll) {
        root.focus({ preventScroll: true });
        root.scrollIntoView({ behavior: "smooth", block: "nearest" });
      }
    } catch (error) {
      if (error.name !== "AbortError")
        root.replaceChildren(
          node("p", "Documento indisponível. Tente atualizar.", "empty-hint"),
        );
      report(error);
    }
  }

  async function showOrder(id, scroll = true) {
    if (!state.token) return;
    state.selectedOrder = id;
    const root = byId("order-detail");
    root.hidden = false;
    root.replaceChildren(node("p", "Carregando pedido…", "help"));
    try {
      const item = await api(
        `/api/pedidos/${encodeURIComponent(id)}`,
        {},
        "order-detail",
      );
      const heading = node("div", null, "detail-heading");
      const title = node("div");
      title.append(
        node("h3", `Pedido #${shortId(item.pedido_id)}`),
        node("p", date(item.criado_em)),
      );
      heading.append(title, badge("Rascunho", "waiting"));
      const wrapper = node("div", null, "table-wrap");
      const table = node("table");
      const caption = node("caption", "Itens do pedido", "sr-only");
      const head = node("thead");
      const headerRow = node("tr");
      for (const label of [
        "Produto",
        "Embalagem",
        "Quantidade",
        "Preço unitário",
        "Subtotal",
      ]) {
        const th = node(
          "th",
          label,
          ["Quantidade", "Preço unitário", "Subtotal"].includes(label)
            ? "numeric"
            : "",
        );
        th.scope = "col";
        headerRow.append(th);
      }
      head.append(headerRow);
      const body = node("tbody");
      for (const product of item.itens) {
        const row = node("tr");
        cell(row, titleCell(product.nome_produto, `EAN ${product.ean}`));
        cell(row, product.embalagem);
        cell(row, `${product.quantidade} ${product.unidade}`, "numeric");
        cell(row, money(product.preco_unitario), "numeric");
        cell(row, money(product.subtotal), "numeric");
        body.append(row);
      }
      table.append(caption, head, body);
      wrapper.append(table);
      const total = node("div", null, "total-row");
      total.append(
        node("span", "Total do rascunho"),
        node("strong", money(item.total)),
      );
      const children = [
        heading,
        reviewStrip(item),
        metadata([
          ["Cliente", item.cliente_nome || "Cliente cadastrado"],
          ["Código do cliente", item.cliente_codigo || "—"],
          ["Referência", item.referencia_documento || "Sem referência"],
          [
            "Documento vinculado",
            item.documento_id ? "Sim" : "Sem documento vinculado",
          ],
        ]),
        wrapper,
        total,
        node(
          "p",
          "Rascunho para conferência. Os valores foram registrados na criação; não há reserva ou baixa de estoque.",
          "info-strip",
        ),
        technicalDetails([
          ["Identificador do pedido", item.pedido_id],
          ["Identificador do cliente", item.cliente_id],
          ["Identificador do documento", item.documento_id || "Sem vínculo"],
        ]),
      ];
      root.replaceChildren(...children.filter(Boolean));
      if (scroll) {
        root.focus({ preventScroll: true });
        root.scrollIntoView({ behavior: "smooth", block: "nearest" });
      }
    } catch (error) {
      if (error.name !== "AbortError")
        root.replaceChildren(
          node("p", "Pedido indisponível. Tente atualizar.", "empty-hint"),
        );
      report(error);
    }
  }

  byId("login-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const token = byId("api-token").value.trim();
    if (!token) return notice("Informe uma chave de acesso.", true);
    const control = byId("login-button");
    control.disabled = true;
    control.textContent = "Conectando…";
    state.generation += 1;
    const generation = state.generation;
    state.token = token;
    notice("");
    try {
      const products = await api(
        "/api/produtos?limit=20&offset=0",
        {},
        "login",
      );
      byId("api-token").value = "";
      byId("login-panel").hidden = true;
      byId("workspace").hidden = false;
      byId("logout").hidden = false;
      state.pages.produtos = {
        offset: 0,
        limit: 20,
        query: "",
        revisao: false,
      };
      byId("produtos-query").value = "";
      openView("produtos", false);
      await loadPage("produtos", products);
    } catch (error) {
      if (generation === state.generation) {
        state.token = "";
        report(error);
      }
    } finally {
      if (generation === state.generation || !state.token) {
        control.disabled = false;
        control.textContent = "Entrar →";
      }
    }
  });

  byId("logout").addEventListener("click", () => logout());
  document
    .querySelectorAll("[data-view]")
    .forEach((element) =>
      element.addEventListener("click", () => openView(element.dataset.view)),
    );
  for (const view of ["produtos", "clientes"]) {
    byId(`${view}-search`).addEventListener("submit", (event) => {
      event.preventDefault();
      state.pages[view].query = byId(`${view}-query`).value.trim();
      state.pages[view].offset = 0;
      notice("");
      loadPage(view);
    });
  }
  document.querySelectorAll("[data-clear]").forEach((element) =>
    element.addEventListener("click", () => {
      const view = element.dataset.clear;
      byId(`${view}-query`).value = "";
      state.pages[view].query = "";
      state.pages[view].offset = 0;
      notice("");
      loadPage(view);
    }),
  );

  byId("upload-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    if (!state.token) return;
    const generation = state.generation;
    const input = byId("document-file");
    const file = input.files[0];
    if (!file)
      return notice("Selecione um PDF, TXT ou imagem para enviar.", true);
    if (!/\.(pdf|txt|png|jpe?g)$/i.test(file.name))
      return notice(
        "Formato não aceito. Escolha um arquivo PDF, TXT, PNG ou JPEG.",
        true,
      );
    if (file.size === 0 || file.size > 10 * 1024 * 1024)
      return notice("O arquivo deve conter dados e ter no máximo 10 MB.", true);
    const form = new FormData();
    form.append("arquivo", file);
    const control = byId("upload-button");
    input.disabled = true;
    control.disabled = true;
    control.textContent = "Enviando…";
    notice("");
    try {
      const uploaded = await api(
        "/api/documentos",
        { method: "POST", body: form },
        "upload",
      );
      input.value = "";
      state.pages.documentos.offset = 0;
      notice("Documento recebido e disponível para processamento.");
      await loadPage("documentos");
      if (generation !== state.generation || !state.token) return;
      await showDocument(uploaded.documento_id);
    } catch (error) {
      report(error);
    } finally {
      if (generation === state.generation || !state.token) {
        input.disabled = false;
        control.disabled = false;
        control.textContent = "Enviar documento ↑";
      }
    }
  });

  async function refresh(view) {
    if (!state.token) return;
    const generation = state.generation;
    const documents = view === "documentos";
    const control = byId(documents ? "refresh-documents" : "refresh-orders");
    control.disabled = true;
    control.textContent = "Atualizando…";
    notice("");
    try {
      const details = documents
        ? state.selectedDocument && showDocument(state.selectedDocument, false)
        : state.selectedOrder && showOrder(state.selectedOrder, false);
      await Promise.all([loadPage(view), details]);
    } finally {
      if (generation === state.generation || !state.token) {
        control.disabled = false;
        control.textContent = "Atualizar";
      }
    }
  }

  for (const view of ["documentos", "pedidos"]) {
    byId(`${view}-revisao`).addEventListener("change", (event) => {
      state.pages[view].revisao = event.target.checked;
      state.pages[view].offset = 0;
      notice("");
      loadPage(view);
    });
  }

  byId("refresh-documents").addEventListener("click", () =>
    refresh("documentos"),
  );
  byId("refresh-orders").addEventListener("click", () => refresh("pedidos"));
})();
