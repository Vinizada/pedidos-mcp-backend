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
      views.map((view) => [view, { offset: 0, limit: 20, query: "" }]),
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
      state.pages[view] = { offset: 0, limit: 20, query: "" };
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
        titleCell(
          item.nome_original,
          item.content_type === "application/pdf"
            ? "Documento PDF"
            : "Documento de texto",
        ),
      );
      cell(row, date(item.criado_em));
      cell(row, size(item.tamanho_bytes));
      cell(row, documentBadge(item.status));
      const open = button("Ver documento", () =>
        showDocument(item.documento_id),
      );
      open.setAttribute("aria-label", `Ver documento ${item.nome_original}`);
      cell(row, open);
      return row;
    },
    pedidos(item) {
      const row = node("tr");
      cell(row, titleCell(`#${shortId(item.pedido_id)}`, "Rascunho", true));
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
    return status === "pedido_criado"
      ? badge("Pedido criado", "success")
      : badge("Aguardando processamento", "waiting");
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
        documentos:
          "Nenhum documento enviado. Selecione um PDF ou TXT acima para começar.",
        pedidos:
          "Nenhum pedido criado. Os pedidos disponíveis aparecerão nesta seção.",
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
        node("p", "Arquivo recebido e disponível para processamento."),
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
      root.replaceChildren(
        heading,
        documentBadge(item.status),
        node("div", null, "detail-spacer"),
        metadata([
          ["Enviado em", date(item.criado_em)],
          [
            "Formato e tamanho",
            `${item.content_type === "application/pdf" ? "PDF" : "TXT"} · ${size(item.tamanho_bytes)}`,
          ],
        ]),
        related,
        technicalDetails([
          ["Identificador do documento", item.documento_id],
          ["SHA-256", item.sha256],
        ]),
      );
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
      root.replaceChildren(
        heading,
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
      );
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
      state.pages.produtos = { offset: 0, limit: 20, query: "" };
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
    if (!file) return notice("Selecione um PDF ou TXT para enviar.", true);
    if (!/\.(pdf|txt)$/i.test(file.name))
      return notice("Formato não aceito. Escolha um arquivo PDF ou TXT.", true);
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

  byId("refresh-documents").addEventListener("click", () =>
    refresh("documentos"),
  );
  byId("refresh-orders").addEventListener("click", () => refresh("pedidos"));
})();
