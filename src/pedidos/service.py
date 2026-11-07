import hashlib
import json
import re
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import func, or_, select, text
from sqlalchemy.orm import Session, joinedload

from pedidos.config import Settings
from pedidos.errors import DomainError
from pedidos.models import (
    Cliente,
    Documento,
    Estoque,
    Pedido,
    PedidoItem,
    Preco,
    Produto,
    ProdutoEmbedding,
)
from pedidos.retrieval import content_hash, embed_texts
from pedidos.schemas import (
    BuscaInput,
    BuscaOutput,
    Candidato,
    CarrinhoInput,
    CarrinhoOutput,
    ClienteDetalhe,
    ClientesOutput,
    ClienteView,
    DisponibilidadeOutput,
    ItemCalculado,
    ListagemInput,
    Page,
    PedidoInput,
    PedidoResumo,
    PedidoView,
    Problema,
    ProdutoIdsInput,
    ProdutoView,
)


class CatalogService:
    def __init__(self, session: Session, settings: Settings):
        self.session = session
        self.settings = settings

    def _views(self, produtos: list[Produto]) -> dict[UUID, ProdutoView]:
        ids = [p.id for p in produtos]
        if not ids:
            return {}
        now = datetime.now(UTC)
        precos = dict(
            self.session.execute(
                select(Preco.produto_id, Preco.valor).where(
                    Preco.produto_id.in_(ids),
                    Preco.inicio_vigencia <= now,
                    or_(Preco.fim_vigencia.is_(None), Preco.fim_vigencia > now),
                )
            ).all()
        )
        estoques = dict(
            self.session.execute(
                select(Estoque.produto_id, Estoque.quantidade - Estoque.reservado).where(
                    Estoque.produto_id.in_(ids)
                )
            ).all()
        )
        return {
            p.id: ProdutoView(
                produto_id=p.id,
                sku=p.sku,
                ean=p.ean,
                nome=p.nome,
                marca=p.marca,
                categoria=p.categoria,
                unidade=p.unidade,
                embalagem=p.embalagem,
                ativo=p.ativo,
                preco_unitario=precos.get(p.id),
                estoque_disponivel=estoques.get(p.id, 0),
            )
            for p in produtos
        }

    def buscar_produtos(self, entrada: BuscaInput) -> BuscaOutput:
        consulta = entrada.consulta
        digits = re.sub(r"[\s.-]", "", consulta)
        origem = None
        if digits.isdigit():
            produtos = list(self.session.scalars(select(Produto).where(Produto.ean == digits)))
            origem = "ean"
        elif re.fullmatch(r"MER-\d+", consulta.upper()):
            produtos = list(
                self.session.scalars(select(Produto).where(Produto.sku == consulta.upper()))
            )
            origem = "sku"
        if origem:
            views = self._views(produtos)
            return BuscaOutput(
                consulta=consulta,
                modo=origem,
                candidatos=[
                    Candidato(**views[p.id].model_dump(), score=1.0, origem=origem)
                    for p in produtos
                ],
            )

        # Full-text português + trigramas para abreviações e erros de digitação.
        documento = func.unaccent(Produto.nome + " " + Produto.descricao_busca)
        query = func.websearch_to_tsquery("portuguese", func.unaccent(consulta))
        vector = func.to_tsvector("portuguese", documento)
        trigram = func.word_similarity(func.unaccent(consulta), documento)
        match = vector.op("@@")(query)
        score = func.ts_rank_cd(vector, query) + trigram
        rows = self.session.execute(
            select(Produto, score.label("score"))
            .where(Produto.ativo.is_(True), or_(match, trigram >= 0.3))
            .order_by(score.desc(), Produto.sku)
            .limit(40)
        ).all()
        produtos = {p.id: p for p, _ in rows}
        scores = {p.id: 1 / (60 + rank) for rank, (p, _) in enumerate(rows, 1)}
        modo = "lexical"
        if self.settings.search_mode == "hybrid":
            indexed = self.session.execute(
                select(Produto, ProdutoEmbedding)
                .join(ProdutoEmbedding, ProdutoEmbedding.produto_id == Produto.id)
                .where(
                    Produto.ativo.is_(True),
                    ProdutoEmbedding.modelo == self.settings.embedding_model,
                )
            ).all()
            total = self.session.scalar(
                select(func.count()).select_from(Produto).where(Produto.ativo.is_(True))
            )
            if len(indexed) != total or any(content_hash(p) != e.hash_conteudo for p, e in indexed):
                raise DomainError("INDICE_INDISPONIVEL", "Execute python -m pedidos.index_catalog.")
            try:
                embedding = embed_texts([consulta], self.settings)[0]
            except Exception as exc:
                raise DomainError(
                    "INDICE_INDISPONIVEL", "Modelo de embeddings indisponível."
                ) from exc
            semantic = self.session.execute(
                select(Produto, ProdutoEmbedding.vetor.cosine_distance(embedding).label("dist"))
                .join(ProdutoEmbedding, ProdutoEmbedding.produto_id == Produto.id)
                .where(
                    Produto.ativo.is_(True),
                    ProdutoEmbedding.modelo == self.settings.embedding_model,
                )
                .order_by("dist", Produto.sku)
                .limit(40)
            ).all()
            # RRF combina ranks; não representa probabilidade nem autoriza substituições.
            for rank, (produto, distance) in enumerate(semantic, 1):
                if distance < 0.65:
                    produtos[produto.id] = produto
                    scores[produto.id] = scores.get(produto.id, 0) + 1 / (60 + rank)
            modo = "hibrida"
        ordered = sorted(scores, key=lambda pid: (-scores[pid], produtos[pid].sku))[
            : entrada.limite
        ]
        views = self._views([produtos[pid] for pid in ordered])
        return BuscaOutput(
            consulta=consulta,
            modo=modo,
            candidatos=[
                Candidato(**views[pid].model_dump(), score=round(scores[pid], 6), origem=modo)
                for pid in ordered
            ],
        )

    def consultar_disponibilidade(self, entrada: ProdutoIdsInput) -> DisponibilidadeOutput:
        produtos = list(
            self.session.scalars(
                select(Produto).where(Produto.id.in_(entrada.produto_ids)).order_by(Produto.sku)
            )
        )
        views = self._views(produtos)
        return DisponibilidadeOutput(
            produtos=list(views.values()),
            nao_encontrados=[pid for pid in dict.fromkeys(entrada.produto_ids) if pid not in views],
        )

    def buscar_cliente(self, entrada: BuscaInput) -> ClientesOutput:
        query = select(Cliente).where(Cliente.ativo.is_(True))
        if re.fullmatch(r"CLI-\d+", entrada.consulta.upper()):
            query = query.where(Cliente.codigo == entrada.consulta.upper())
        else:
            query = query.where(
                func.strpos(
                    func.lower(func.unaccent(Cliente.nome)),
                    func.lower(func.unaccent(entrada.consulta)),
                )
                > 0
            )
        rows = self.session.scalars(query.order_by(Cliente.codigo).limit(entrada.limite))
        return ClientesOutput(
            clientes=[
                ClienteView(cliente_id=c.id, codigo=c.codigo, nome=c.nome, ativo=c.ativo)
                for c in rows
            ]
        )

    def validar_carrinho(self, entrada: CarrinhoInput) -> CarrinhoOutput:
        problemas = []
        cliente = self.session.get(Cliente, entrada.cliente_id)
        if cliente is None or not cliente.ativo:
            problemas.append(
                Problema(codigo="CLIENTE_INVALIDO", mensagem="Cliente não existe ou inativo.")
            )
        produtos = list(
            self.session.scalars(
                select(Produto).where(Produto.id.in_([item.produto_id for item in entrada.itens]))
            )
        )
        views = self._views(produtos)
        itens = []
        for item in entrada.itens:
            produto = views.get(item.produto_id)
            codigo = mensagem = None
            if produto is None:
                codigo, mensagem = "PRODUTO_INEXISTENTE", "Produto não cadastrado."
            elif not produto.ativo:
                codigo, mensagem = "PRODUTO_INATIVO", "Produto inativo."
            elif produto.preco_unitario is None:
                codigo, mensagem = "SEM_PRECO", "Produto sem preço vigente."
            elif produto.estoque_disponivel < item.quantidade:
                codigo = "ESTOQUE_INSUFICIENTE"
                mensagem = f"Disponível: {produto.estoque_disponivel} UN da embalagem cadastrada."
            if codigo:
                problemas.append(
                    Problema(codigo=codigo, mensagem=mensagem, produto_id=item.produto_id)
                )
                continue
            itens.append(
                ItemCalculado(
                    produto_id=produto.produto_id,
                    ean=produto.ean,
                    nome_produto=produto.nome,
                    unidade=produto.unidade,
                    embalagem=produto.embalagem,
                    quantidade=item.quantidade,
                    preco_unitario=produto.preco_unitario,
                    subtotal=produto.preco_unitario * item.quantidade,
                )
            )
        return CarrinhoOutput(
            valido=not problemas,
            cliente_id=entrada.cliente_id,
            itens=itens,
            total=None if problemas else sum((i.subtotal for i in itens), Decimal("0.00")),
            problemas=problemas,
        )

    def criar_pedido_rascunho(self, entrada: PedidoInput) -> PedidoView:
        payload = entrada.model_dump(mode="json", exclude={"chave_idempotencia"})
        # Compatibilidade com hashes de pedidos criados antes da migração de documentos.
        if payload["documento_id"] is None:
            payload.pop("documento_id")
        payload["itens"] = sorted(payload["itens"], key=lambda i: i["produto_id"])
        fingerprint = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
        lock = int.from_bytes(
            hashlib.sha256(entrada.chave_idempotencia.encode()).digest()[:8], "big", signed=True
        )
        try:
            # Serializa retries concorrentes antes da consulta de idempotência.
            self.session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": lock})
            pedido = self.session.scalar(
                select(Pedido).where(Pedido.chave_idempotencia == entrada.chave_idempotencia)
            )
            if pedido:
                if pedido.hash_requisicao != fingerprint:
                    raise DomainError("IDEMPOTENCIA_CONFLITO", "Chave já usada com outro conteúdo.")
                result = self.consultar_pedido(pedido.id)
                result.repeticao_idempotente = True
                self.session.commit()
                return result
            if (
                entrada.documento_id is not None
                and self.session.get(Documento, entrada.documento_id) is None
            ):
                raise DomainError("DOCUMENTO_NAO_ENCONTRADO", "Documento não encontrado.")
            validacao = self.validar_carrinho(entrada)
            if not validacao.valido:
                raise DomainError(
                    "CARRINHO_INVALIDO",
                    "Corrija o carrinho antes de criar o rascunho.",
                    validacao.problemas,
                )
            pedido = Pedido(
                cliente_id=entrada.cliente_id,
                total=validacao.total,
                status="rascunho",
                chave_idempotencia=entrada.chave_idempotencia,
                hash_requisicao=fingerprint,
                referencia_documento=entrada.referencia_documento,
                documento_id=entrada.documento_id,
            )
            self.session.add(pedido)
            self.session.flush()
            self.session.add_all(
                [PedidoItem(pedido_id=pedido.id, **item.model_dump()) for item in validacao.itens]
            )
            self.session.flush()
            result = self.consultar_pedido(pedido.id)
            self.session.commit()
            return result
        except Exception:
            self.session.rollback()
            raise

    def consultar_pedido(self, pedido_id: UUID) -> PedidoView:
        pedido = self.session.scalar(
            select(Pedido).options(joinedload(Pedido.cliente)).where(Pedido.id == pedido_id)
        )
        if pedido is None:
            raise DomainError("PEDIDO_NAO_ENCONTRADO", "Pedido não encontrado.")
        itens = self.session.scalars(
            select(PedidoItem)
            .where(PedidoItem.pedido_id == pedido_id)
            .order_by(PedidoItem.produto_id)
        )
        return PedidoView(
            pedido_id=pedido.id,
            cliente_id=pedido.cliente_id,
            cliente_nome=pedido.cliente.nome,
            cliente_codigo=pedido.cliente.codigo,
            status=pedido.status,
            criado_em=pedido.criado_em,
            total=pedido.total,
            referencia_documento=pedido.referencia_documento,
            documento_id=pedido.documento_id,
            itens=[
                ItemCalculado(**{name: getattr(i, name) for name in ItemCalculado.model_fields})
                for i in itens
            ],
        )

    def listar_produtos(self, entrada: ListagemInput) -> Page[ProdutoView]:
        query = select(Produto)
        if entrada.consulta:
            content = Produto.nome + " " + Produto.sku + " " + Produto.ean + " " + Produto.marca
            query = query.where(
                func.strpos(
                    func.lower(func.unaccent(content)), func.lower(func.unaccent(entrada.consulta))
                )
                > 0
            )
        total = self.session.scalar(select(func.count()).select_from(query.subquery()))
        produtos = list(
            self.session.scalars(
                query.order_by(Produto.sku).limit(entrada.limit).offset(entrada.offset)
            )
        )
        return Page(
            items=list(self._views(produtos).values()),
            total=total,
            limit=entrada.limit,
            offset=entrada.offset,
        )

    def listar_clientes(self, entrada: ListagemInput) -> Page[ClienteDetalhe]:
        query = select(Cliente)
        if entrada.consulta:
            content = Cliente.nome + " " + Cliente.codigo
            query = query.where(
                func.strpos(
                    func.lower(func.unaccent(content)), func.lower(func.unaccent(entrada.consulta))
                )
                > 0
            )
        total = self.session.scalar(select(func.count()).select_from(query.subquery()))
        clientes = self.session.scalars(
            query.order_by(Cliente.codigo).limit(entrada.limit).offset(entrada.offset)
        )
        return Page(
            items=[
                ClienteDetalhe(
                    cliente_id=c.id, codigo=c.codigo, nome=c.nome, email=c.email, ativo=c.ativo
                )
                for c in clientes
            ],
            total=total,
            limit=entrada.limit,
            offset=entrada.offset,
        )

    def listar_pedidos(
        self, entrada: ListagemInput, documento_id: UUID | None = None
    ) -> Page[PedidoResumo]:
        query = select(Pedido).options(joinedload(Pedido.cliente))
        if documento_id is not None:
            query = query.where(Pedido.documento_id == documento_id)
        total = self.session.scalar(select(func.count()).select_from(query.subquery()))
        pedidos = self.session.scalars(
            query.order_by(Pedido.criado_em.desc(), Pedido.id.desc())
            .limit(entrada.limit)
            .offset(entrada.offset)
        )
        return Page(
            items=[
                PedidoResumo(
                    pedido_id=p.id,
                    cliente_id=p.cliente_id,
                    cliente_nome=p.cliente.nome,
                    cliente_codigo=p.cliente.codigo,
                    documento_id=p.documento_id,
                    status=p.status,
                    total=p.total,
                    criado_em=p.criado_em,
                )
                for p in pedidos
            ],
            total=total,
            limit=entrada.limit,
            offset=entrada.offset,
        )
