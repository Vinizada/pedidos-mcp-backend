
import { test, expect } from '@playwright/test';

test.describe('Funcionalidade: Pedidos', () => {

  test('PW-004 - Consultar e validar detalhes de um pedido', async ({ page }) => {

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
    // 3. Acessar módulo de Pedidos
    // ==========================================

    await page.getByRole('button', {
      name: 'Pedidos'
    }).click();

    // ==========================================
    // 4. Localizar pedido
    // ==========================================

    const botaoPedido = page.getByRole('button', {
      name: 'Ver pedido 3e20d06c'
    });

    await expect(botaoPedido).toBeVisible();

    await botaoPedido.click();

    // ==========================================
    // 5. Validar detalhes do pedido
    // ==========================================

    const detalhesPedido = page.getByRole('region', {
      name: 'Detalhes do pedido'
    });

    await expect(detalhesPedido).toBeVisible();

    // ==========================================
    // 6. Validar identificação do cliente
    // ==========================================

    await expect(
      detalhesPedido.getByText(/CLI-\d+/)
    ).toBeVisible();

    // ==========================================
    // 7. Validar produtos do pedido
    // ==========================================

    await expect(
      detalhesPedido.getByText('Arroz branco tipo 1 5 kg')
    ).toBeVisible();

    await expect(
      detalhesPedido.getByText('Feijão preto 1 kg')
    ).toBeVisible();

  });

});
