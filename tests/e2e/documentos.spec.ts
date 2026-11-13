
import { test, expect } from '@playwright/test';

test.describe('Funcionalidade: Documentos', () => {

  test('PW-003 - Consultar documento TXT processado e validar pedido', async ({ page }) => {

    test.setTimeout(60000);

    // ==========================================
    // 1. Acessar aplicação
    // ==========================================

    await page.goto('/interface');

    // ==========================================
    // 2. Autenticação
    // ==========================================

    const apiToken = process.env.API_TOKEN;

    if (!apiToken) {
      throw new Error('Variável API_TOKEN não configurada.');
    }

    await page.getByRole('textbox', {
      name: 'Chave de acesso'
    }).fill(apiToken);

    await page.getByRole('button', {
      name: 'Entrar'
    }).click();

    // ==========================================
    // 3. Acessar Documentos
    // ==========================================

    await page.getByRole('button', {
      name: 'Documentos'
    }).click();

    // ==========================================
    // 4. Atualizar listagem
    // ==========================================

    await page.getByRole('button', {
      name: 'Atualizar'
    }).click();

    // ==========================================
    // 5. Localizar documento mais recente
    // ==========================================

    const tabelaDocumentos = page.getByRole('table', {
      name: 'Documentos enviados'
    });

    const linhasDocumento = tabelaDocumentos
      .getByRole('row')
      .filter({
        has: page.getByRole('button', {
          name: 'Ver documento pedido-002.txt'
        })
      });

    // A listagem está ordenada do mais recente para o mais antigo
    const linhaDocumento = linhasDocumento.first();

    await expect(linhaDocumento).toBeVisible();

    // Validar situação do documento
    await expect(
      linhaDocumento.getByText('Pedido criado', {
        exact: true
      })
    ).toBeVisible();

    // ==========================================
    // 6. Abrir detalhes do documento
    // ==========================================

    await linhaDocumento.getByRole('button', {
      name: 'Ver documento pedido-002.txt'
    }).click();

    const detalhesDocumento = page.getByRole('region', {
      name: 'Detalhes do documento'
    });

    await expect(detalhesDocumento).toBeVisible();

    await expect(
      detalhesDocumento.getByRole('heading', {
        name: 'pedido-002.txt'
      })
    ).toBeVisible();

    // ==========================================
    // 7. Validar processamento da IA
    // ==========================================

    await expect(
      detalhesDocumento.getByText(
        'Arquivo processado. Confira o resultado abaixo.',
        { exact: true }
      )
    ).toBeVisible();

    await expect(
      detalhesDocumento.getByRole('heading', {
        name: 'Resultado do processamento'
      })
    ).toBeVisible();

    await expect(
      detalhesDocumento.getByText('Rascunho criado', {
        exact: true
      })
    ).toBeVisible();

    // ==========================================
    // 8. Acessar pedido vinculado
    // ==========================================

    await detalhesDocumento.getByRole('button', {
      name: /Conferir pedido/
    }).click();

    // ==========================================
    // 9. Validar detalhes do pedido
    // ==========================================

    const detalhesPedido = page.getByRole('region', {
      name: 'Detalhes do pedido'
    });

    await expect(detalhesPedido).toBeVisible();

    // Validar cliente
    await expect(
      detalhesPedido.getByText('CLI-002', {
        exact: true
      })
    ).toBeVisible();

    // Validar produtos
    await expect(
      detalhesPedido.getByText('Arroz branco tipo 1 5 kg')
    ).toBeVisible();

    await expect(
      detalhesPedido.getByText('Feijão preto 1 kg')
    ).toBeVisible();

    // Validar valor total
    await expect(
      detalhesPedido.getByText('R$ 162,60', {
        exact: true
      })
    ).toBeVisible();

  });

});
