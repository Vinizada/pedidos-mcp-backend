
import { test, expect } from '@playwright/test';

test.describe('Catálogo de Produtos', () => {

  test('PW-001 - Consultar produto por EAN', async ({ page }) => {

    // Acessar aplicação
    await page.goto('/interface');

    // Autenticação utilizando variável de ambiente
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

    // Acessar Produtos
    await page.getByRole('button', {
      name: 'Produtos'
    }).click();

    // Pesquisar pelo EAN
    await page.getByRole('searchbox', {
      name: 'Pesquisar produtos'
    }).fill('2990000000019');

    await page.getByRole('button', {
      name: 'Buscar'
    }).click();

    // Validar resultado
    await expect(
      page.getByText('Café moído tradicional 500 g')
    ).toBeVisible();

    await expect(
      page.getByText('2990000000019')
    ).toBeVisible();

    await expect(
      page.getByText('MER-001')
    ).toBeVisible();

    await expect(
      page.getByText('R$ 18,90')
    ).toBeVisible();

  });

});
