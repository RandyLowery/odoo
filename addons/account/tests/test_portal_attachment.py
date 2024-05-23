# -*- coding: utf-8 -*-
# Part of Odoo. See LICENSE file for full copyright and licensing details.
from odoo.addons.account.tests.common import AccountTestInvoicingHttpCommon
from odoo.tests.common import tagged

import json

from odoo import http
from odoo.tools import mute_logger


@tagged('post_install', '-at_install')
class TestPortalAttachment(AccountTestInvoicingHttpCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()

        cls.out_invoice = cls.env['account.move'].with_context(tracking_disable=True).create({
            'move_type': 'out_invoice',
            'partner_id': cls.partner_a.id,
            'invoice_date': '2019-05-01',
            'date': '2019-05-01',
            'invoice_line_ids': [
                (0, 0, {'name': 'line1', 'price_unit': 100.0}),
            ],
        })

        cls.invoice_base_url = cls.out_invoice.get_base_url()

    @mute_logger('odoo.addons.http_routing.models.ir_http', 'odoo.http')
    def test_01_portal_attachment(self):
        """Test the portal chatter attachment route."""
        self.partner_a.write({  # ensure an email for message_post
            'email': 'partner.a@test.example.com',
        })

        self.authenticate(None, None)

        # Test public user can't create attachment without token of document
        res = self.url_open(
            url=f'{self.invoice_base_url}/portal/attachment/add',
            data={
                'name': "new attachment",
                'thread_model': self.out_invoice._name,
                'thread_id': self.out_invoice.id,
                'csrf_token': http.Request.csrf_token(self),
            },
            files=[('file', ('test.txt', b'test', 'plain/text'))],
        )
        self.assertEqual(res.status_code, 400)
        self.assertIn("you do not have the rights", res.text)

        # Test public user can create attachment with token
        res = self.url_open(
            url=f'{self.invoice_base_url}/portal/attachment/add',
            data={
                'name': "new attachment",
                'thread_model': self.out_invoice._name,
                'thread_id': self.out_invoice.id,
                'csrf_token': http.Request.csrf_token(self),
                'access_token': self.out_invoice._portal_ensure_token(),
            },
            files=[('file', ('test.txt', b'test', 'plain/text'))],
        )
        self.assertEqual(res.status_code, 200)
        create_res = json.loads(res.content.decode('utf-8'))
        self.assertTrue(self.env['ir.attachment'].sudo().search([('id', '=', create_res['id'])]))

        # Test created attachment is private
        res_binary = self.url_open('/web/content/%d' % create_res['id'])
        self.assertEqual(res_binary.status_code, 404)

        # Test created access_token is working
        res_binary = self.url_open('/web/content/%d?access_token=%s' % (create_res['id'], create_res['access_token']))
        self.assertEqual(res_binary.status_code, 200)

        # Test mimetype is neutered as non-admin
        res = self.url_open(
            url=f'{self.invoice_base_url}/portal/attachment/add',
            data={
                'name': "new attachment",
                'thread_model': self.out_invoice._name,
                'thread_id': self.out_invoice.id,
                'csrf_token': http.Request.csrf_token(self),
                'access_token': self.out_invoice._portal_ensure_token(),
            },
            files=[('file', ('test.svg', b'<svg></svg>', 'image/svg+xml'))],
        )
        self.assertEqual(res.status_code, 200)
        create_res = json.loads(res.content.decode('utf-8'))
        self.assertEqual(create_res['mimetype'], 'text/plain')

        res_binary = self.url_open('/web/content/%d?access_token=%s' % (create_res['id'], create_res['access_token']))
        self.assertEqual(res_binary.headers['Content-Type'], 'text/plain; charset=utf-8')
        self.assertEqual(res_binary.content, b'<svg></svg>')

        res_image = self.url_open('/web/image/%d?access_token=%s' % (create_res['id'], create_res['access_token']))
        self.assertEqual(res_image.headers['Content-Type'], 'application/octet-stream')
        self.assertEqual(res_image.content, b'<svg></svg>')

        # Test attachment can't be removed without valid token
        res = self.opener.post(
            url=f'{self.invoice_base_url}/portal/attachment/remove',
            json={
                'params': {
                    'attachment_id': create_res['id'],
                    'access_token': "wrong",
                },
            },
        )
        self.assertEqual(res.status_code, 200)
        self.assertTrue(self.env['ir.attachment'].sudo().search([('id', '=', create_res['id'])]))
        self.assertIn("you do not have the rights", res.text)

        # Test attachment can be removed with token if "pending" state
        res = self.opener.post(
            url=f'{self.invoice_base_url}/portal/attachment/remove',
            json={
                'params': {
                    'attachment_id': create_res['id'],
                    'access_token': create_res['access_token'],
                },
            },
        )
        self.assertEqual(res.status_code, 200)
        remove_res = json.loads(res.content.decode('utf-8'))['result']
        self.assertFalse(self.env['ir.attachment'].sudo().search([('id', '=', create_res['id'])]))
        self.assertTrue(remove_res is True)

        # Test attachment can't be removed if not "pending" state
        attachment = self.env['ir.attachment'].create({
            'name': 'an attachment',
            'access_token': self.env['ir.attachment']._generate_access_token(),
        })
        res = self.opener.post(
            url=f'{self.invoice_base_url}/portal/attachment/remove',
            json={
                'params': {
                    'attachment_id': attachment.id,
                    'access_token': attachment.access_token,
                },
            },
        )
        self.assertEqual(res.status_code, 200)
        self.assertTrue(self.env['ir.attachment'].sudo().search([('id', '=', attachment.id)]))
        self.assertIn("not in a pending state", res.text)

        # Test attachment can't be removed if attached to a message
        attachment.write({
            'res_model': 'mail.compose.message',
            'res_id': 0,
        })
        attachment.flush_recordset()
        message = self.env['mail.message'].create({
            'attachment_ids': [(6, 0, attachment.ids)],
        })
        res = self.opener.post(
            url=f'{self.invoice_base_url}/portal/attachment/remove',
            json={
                'params': {
                    'attachment_id': attachment.id,
                    'access_token': attachment.access_token,
                },
            },
        )
        self.assertEqual(res.status_code, 200)
        self.assertTrue(attachment.exists())
        self.assertIn("it is linked to a message", res.text)
        message.sudo().unlink()

        # Test attachment can't be associated if no attachment token.
        res = self.opener.post(
            url=f'{self.invoice_base_url}/mail/message/post',
            json={
                'params': {
                    'thread_model': self.out_invoice._name,
                    'thread_id': self.out_invoice.id,
                    'post_data': {
                        'body': "test message 1",
                        'attachment_ids': [attachment.id],
                    },
                    'attachment_tokens': ['false'],
                },
            },
        )
        self.assertEqual(res.status_code, 200)
        self.assertIn("The attachment %s does not exist or you do not have the rights to access it." % attachment.id, res.text)

        # Test attachment can't be associated if no main document token
        res = self.opener.post(
            url=f'{self.invoice_base_url}/mail/message/post',
            json={
                'params': {
                    'thread_model': self.out_invoice._name,
                    'thread_id': self.out_invoice.id,
                    'post_data': {'body': "test message 1", 'attachment_ids': [attachment.id]},
                    'attachment_tokens': [attachment.access_token],
                },
            },
        )
        self.assertEqual(res.status_code, 200)
        self.assertIn("The requested URL was not found on the server.", res.text)

        # Test attachment can't be associated if not "pending" state
        self.assertFalse(self.out_invoice.message_ids)
        attachment.write({'res_model': 'model'})
        res = self.opener.post(
            url=f'{self.invoice_base_url}/mail/message/post',
            json={
                'params': {
                    'thread_model': self.out_invoice._name,
                    'thread_id': self.out_invoice.id,
                    'post_data': {'body': "test message 1", 'attachment_ids': [attachment.id]},
                    'attachment_tokens': [attachment.access_token],
                    'token': self.out_invoice._portal_ensure_token(),
                },
            },
        )
        self.assertEqual(res.status_code, 200)
        self.out_invoice.invalidate_recordset(['message_ids'])
        self.assertEqual(len(self.out_invoice.message_ids), 1)
        self.assertEqual(self.out_invoice.message_ids.body, "<p>test message 1</p>")
        self.assertFalse(self.out_invoice.message_ids.attachment_ids)

        # Test attachment can't be associated if not correct user
        attachment.write({'res_model': 'mail.compose.message'})
        res = self.opener.post(
            url=f'{self.invoice_base_url}/mail/message/post',
            json={
                'params': {
                    'thread_model': self.out_invoice._name,
                    'thread_id': self.out_invoice.id,
                    'post_data': {'body': "test message 2", 'attachment_ids': [attachment.id]},
                    'attachment_tokens': [attachment.access_token],
                    'token': self.out_invoice._portal_ensure_token(),
                },
            },
        )
        self.assertEqual(res.status_code, 200)
        self.out_invoice.invalidate_recordset(['message_ids'])
        self.assertEqual(len(self.out_invoice.message_ids), 2)
        self.assertEqual(self.out_invoice.message_ids[0].author_id, self.partner_a)
        self.assertEqual(self.out_invoice.message_ids[0].body, "<p>test message 2</p>")
        self.assertEqual(self.out_invoice.message_ids[0].email_from, self.partner_a.email_formatted)
        self.assertFalse(self.out_invoice.message_ids.attachment_ids)

        # Test attachment can be associated if all good (complete flow)
        res = self.url_open(
            url=f'{self.invoice_base_url}/portal/attachment/add',
            data={
                'name': "final attachment",
                'thread_model': self.out_invoice._name,
                'thread_id': self.out_invoice.id,
                'csrf_token': http.Request.csrf_token(self),
                'access_token': self.out_invoice._portal_ensure_token(),
            },
            files=[('file', ('test.txt', b'test', 'plain/text'))],
        )
        self.assertEqual(res.status_code, 200)
        create_res = json.loads(res.content.decode('utf-8'))
        self.assertEqual(create_res['name'], "final attachment")

        res = self.opener.post(
            url=f'{self.invoice_base_url}/mail/message/post',
            json={
                'params': {
                    'thread_model': self.out_invoice._name,
                    'thread_id': self.out_invoice.id,
                    'post_data': {'body': "test message 3", 'attachment_ids': [create_res['id']]},
                    'attachment_tokens': [create_res['access_token']],
                    'token': self.out_invoice._portal_ensure_token(),
                },
            },
        )
        self.assertEqual(res.status_code, 200)
        self.out_invoice.invalidate_recordset(['message_ids'])
        self.assertEqual(len(self.out_invoice.message_ids), 3)
        self.assertEqual(self.out_invoice.message_ids[0].body, "<p>test message 3</p>")
        self.assertEqual(len(self.out_invoice.message_ids[0].attachment_ids), 1)
