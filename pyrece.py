#!usr/bin/python
# -*- coding: latin-1 -*-
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by the
# Free Software Foundation; either version 3, or (at your option) any later
# version.
#
# This program is distributed in the hope that it will be useful, but
# WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTIBILITY
# or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU General Public License
# for more details.

"Aplicativo AdHoc Para generaci�n de Facturas Electr�nicas"

__author__ = "Mariano Reingart (mariano@nsis.com.ar)"
__copyright__ = "Copyright (C) 2009 Mariano Reingart"
__license__ = "GPL 3.0"
__version__ = "1.16"

import csv
from decimal import Decimal
import os
import sys
import wx
from PythonCard import dialog, model
import traceback
from ConfigParser import SafeConfigParser
import wsaa,wsfe
from php import SimpleXMLElement, SoapClient, SoapFault, date
from email.mime.text import MIMEText
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from smtplib import SMTP

from PyFPDF.ejemplos.form import Form

HOMO = False
DEBUG = False
CONFIG_FILE = "rece.ini"

def digito_verificador_modulo10(codigo):
    "Rutina para el c�lculo del d�gito verificador 'm�dulo 10'"
    # http://www.consejo.org.ar/Bib_elect/diciembre04_CT/documentos/rafip1702.htm
    # Etapa 1: comenzar desde la izquierda, sumar todos los caracteres ubicados en las posiciones impares.
    etapa1 = sum([int(c) for i,c in enumerate(codigo) if not i%2])
    # Etapa 2: multiplicar la suma obtenida en la etapa 1 por el n�mero 3
    etapa2 = etapa1 * 3
    # Etapa 3: comenzar desde la izquierda, sumar todos los caracteres que est�n ubicados en las posiciones pares.
    etapa3 = sum([int(c) for i,c in enumerate(codigo) if i%2])
    # Etapa 4: sumar los resultados obtenidos en las etapas 2 y 3.
    etapa4 = etapa2 + etapa3
    # Etapa 5: buscar el menor n�mero que sumado al resultado obtenido en la etapa 4 d� un n�mero m�ltiplo de 10. Este ser� el valor del d�gito verificador del m�dulo 10.
    digito = 10 - (etapa4 - (int(etapa4 / 10) * 10))
    if digito == 10:
        digito = 0
    return str(digito)


class PyRece(model.Background):

    def on_initialize(self, event):
        self.cols = []
        self.items = []
        self.paths = [entrada]
        self.token = self.sign = ""
        self.client = SoapClient(wsfe_url, action=wsfe.SOAP_ACTION, namespace=wsfe.SOAP_NS,
                                trace=False, exceptions=True)
        self.smtp = None
    
    def set_cols(self, cols):
        self.__cols = cols
        self.components.lvwListado.columnHeadings = [col.replace("_"," ").title() for col in cols]
    def get_cols(self):
        return self.__cols
    cols = property(get_cols, set_cols)

    def set_items(self, items):
        cols = self.cols
        self.__items = items
        self.components.lvwListado.items = [[str(item[col]) for col in cols] for item in items]
        wx.SafeYield()
    def get_items(self):
        return self.__items
    items = property(get_items, set_items)

    def get_selected_items(self):
        itemidx = -1
        itemidx = self.components.lvwListado.GetNextItem(itemidx, wx.LIST_NEXT_ALL, wx.LIST_STATE_SELECTED)
        while itemidx >= 0:
            yield itemidx, self.__items[itemidx]
            itemidx = self.components.lvwListado.GetNextItem(itemidx, wx.LIST_NEXT_ALL, wx.LIST_STATE_SELECTED)

    def set_paths(self, paths):
        self.__paths = paths
        self.components.txtArchivo.text = ', '.join([fn for fn in paths])
    def get_paths(self):
        return self.__paths
    paths = property(get_paths, set_paths)
        
    def log(self, msg):
        self.components.txtEstado.text = msg+ u"\n" + self.components.txtEstado.text
        wx.SafeYield()
    
    def progreso(self, value):
        per = (value+1)/float(len(self.items))*100
        self.components.pbProgreso.value = per
        wx.SafeYield()

    def error(self, code, text):
        ex = traceback.format_exception( sys.exc_type, sys.exc_value, sys.exc_traceback)
        self.log(''.join(ex))
        dialog.alertDialog(self, text, 'Error %s' % code)

    def on_btnMarcarTodo_mouseClick(self, event):
        for i in range(len(self.__items)):
            self.components.lvwListado.SetSelection(i)

    def on_menuConsultasLastCBTE_select(self, event):
        tipos = {
            1:u"Factura A",
            2:u"Notas de D�bito A",
            3:u"Notas de Cr�dito A",
            4:u"Recibos A",
            5:u"Notas de Venta al contado A",
            6:u"Facturas B",
            7:u"Notas de D�bito B",
            8:u"uNotas de Cr�dito B",
            9:u"uRecibos B",
            10:u"Notas de Venta al contado B",
            39:u"Otros comprobantes A que cumplan con la R.G. N� 3419",
            40:u"Otros comprobantes B que cumplan con la R.G. N� 3419",
            60:u"Cuenta de Venta y L�quido producto A",
            61:u"Cuenta de Venta y L�quido producto B",
            63:u"Liquidaci�n A",
            64:u"Liquidaci�n B"}

        result = dialog.singleChoiceDialog(self, "Tipo de comprobante", 
            "Consulta �ltimo Nro. Comprobante", 
                [v for k,v in sorted([(k,v) for k,v in tipos.items()])])
        if not result.accepted:
            return
        tipocbte = [k for k,v in tipos.items() if v==result.selection][0]
        result = dialog.textEntryDialog(self, "Punto de venta",
            "Consulta �ltimo Nro. Comprobante", '2')
        if not result.accepted:
            return
        ptovta = result.text

        try:
            ultcmp = wsfe.recuperar_last_cmp(self.client, self.token, self.sign, 
                cuit, ptovta, tipocbte)
            dialog.alertDialog(self, u"�ltimo comprobante: %s\n" 
                "Tipo: %s (%s)\nPunto de Venta: %s" % (ultcmp, tipos[tipocbte], 
                    tipocbte, ptovta), 'Consulta �ltimo Nro. Comprobante')
        except SoapFault,e:
            self.log(self.client.xml_request)
            self.log(self.client.xml_response)
            self.error(e.faultcode, e.faultstring.encode("ascii","ignore"))
        except wsfe.WSFEError,e:
            self.error(e.code, e.msg.encode("ascii","ignore"))
        except Exception, e:
            self.error('Excepci�n',unicode(str(e),"latin1","ignore"))

    def on_menuConsultasLastID_select(self, event):
        try:
            ultnro = wsfe.ultnro(self.client, self.token, self.sign, cuit)
            dialog.alertDialog(self, "�ltimo ID (m�ximo): %s" % (ultnro), 
                'Consulta �ltimo ID')
        except SoapFault,e:
            self.log(self.client.xml_request)
            self.log(self.client.xml_response)
            self.error(e.faultcode, e.faultstring.encode("ascii","ignore"))
        except wsfe.WSFEError,e:
            self.error(e.code, e.msg.encode("ascii","ignore"))
        except Exception, e:
            self.error('Excepci�n',unicode(e))


    def on_btnAyuda_mouseClick(self, event):
        text = """
PyRece: Aplicativo AdHoc para generar Facturas Electr�nicas
Copyright (C) 2008/2009 Mariano Reingart reingart@gmail.com

Este progarma es software libre, se entrega ABSOLUTAMENTE SIN GARANTIA
y es bienvenido a redistribuirlo bajo la licencia GPLv3.

Para informaci�n adicional y descargas ver:
http://www.sistemasagiles.com.ar/

Forma de uso:

 * Examinar: para buscar el archivo a procesar (opcional)
 * Cargar: para leer los datos del archivo de facturas a procesar 
 * Autenticar: para iniciar la sesi�n en los servidores de AFIP (obligatorio antes de autorizar)
 * Marcar Todo: para seleccionar todas las facturas
 * Autorizar: para autorizar las facturas seleccionadas, completando el CAE y dem�s datos
 * Autorizar Lote: para autorizar en un solo lote las facturas seleccionadas
 * Grabar: para almacenar los datos procesados en el archivo de facturas 
 * Previsualizar: para ver por pantalla la factura seleccionadas
 * Enviar: para envia por correo electr�nico las facturas seleccionadas

Para solicitar soporte comercial, escriba a pyafipws@nsis.com.ar
"""
        dialog.alertDialog(self, text, 'Ayuda')

    def on_btnLimpiar_mouseClick(self, event):
        self.components.txtEstado.text = ""

    def on_btnAutenticar_mouseClick(self, event):
        try:
            self.log("Creando TRA...")
            tra = wsaa.create_tra()
            self.log("Frimando TRA (CMS)...")
            cms = wsaa.sign_tra(str(tra),str(cert),str(privatekey))
            self.log("Llamando a WSAA...")
            xml = wsaa.call_wsaa(str(cms),wsaa_url)
            self.log("Procesando respuesta...")
            ta = SimpleXMLElement(xml)
            self.token = str(ta.credentials.token)
            self.sign = str(ta.credentials.sign)
            self.log("Token: %s" % self.token)
            self.log("Sign: %s" % self.sign)
            dialog.alertDialog(self, 'Autenticado OK!', 'Advertencia')
        except SoapFault,e:
            self.error(e.faultcode, e.faultstring.encode("ascii","ignore"))
        except Exception, e:
            self.error('Excepci�n',unicode(e))
    
    def on_btnExaminar_mouseClick(self, event):
        wildcard = "Archivos CSV (*.csv)|*.csv"
        result = dialog.fileDialog(self, 'Abrir', '', '', wildcard )
        if not result.accepted:
            return
        self.paths = result.paths

    def on_btnCargar_mouseClick(self, event):
        items = []
        for fn in self.paths:
            csv_reader = csv.reader(open(fn), dialect='excel', delimiter=";")
            for row in csv_reader:
                items.append(row)
        if len(items) < 2:
            dialog.alertDialog(self, 'El archivo no tiene datos v�lidos', 'Advertencia')
        cols = [str(it).strip() for it in items[0]]
        # armar diccionario por cada linea
        items = [dict([(cols[i],str(v).strip()) for i,v in enumerate(item)]) for item in items[1:]]
        self.cols = cols
        self.items = items

    def on_btnGrabar_mouseClick(self, event):
        try:
            wildcard = "Archivos CSV (*.csv)|*.csv"
            if self.paths:
                path = self.paths[0]
            else:
                path = salida
            result = dialog.saveFileDialog(self, title='Guardar', filename=path, 
                wildcard=wildcard )
            if not result.accepted:
                return
            fn = result.paths[0]
            f = open(fn,"wb")
            csv_writer = csv.writer(f, dialect='excel', delimiter=";")
            csv_writer.writerows([self.cols])
            csv_writer.writerows([[item[k] for k in self.cols] for item in self.items])
            f.close()
            dialog.alertDialog(self, u'Se guard� con �xito el archivo:\n%s' % (unicode(fn),), 'Guardar')
        except Exception, e:
            self.error('Excepci�n',unicode(e))

    def on_btnAutorizar_mouseClick(self, event):
        try:
            ok = 0
            rechazadas = 0
            cols = self.cols
            items = []
            self.progreso(0)
            for i, kargs in self.get_selected_items():
                kargs['cbt_desde'] = kargs['cbt_hasta'] = kargs ['cbt_numero']
                if 'id' not in kargs or kargs['id'] == "":
                    id = long(kargs['cbt_desde'])
                    id += (int(kargs['tipo_cbte'])*10**4 + int(kargs['punto_vta']))*10**8
                    kargs['id'] = id
                for key in kargs:
                    if isinstance(kargs[key], basestring):
                        kargs[key] = kargs[key].replace(",",".")
                if DEBUG:
                    self.log('\n'.join(["%s='%s'" % (k,v) for k,v in kargs.items()]))
                ret = wsfe.aut(self.client, self.token, self.sign, cuit, **kargs)
                kargs.update(ret)
                del kargs['cbt_desde'] 
                del kargs['cbt_hasta']
                self.items[i] = kargs
                self.log("ID: %s CAE: %s Motivo: %s Reproceso: %s" % (kargs['id'], kargs['cae'], kargs['motivo'],kargs['reproceso']))
                if kargs['resultado'] == "R":
                    rechazadas += 1
                else:
                    ok += 1
                self.progreso(i)
            self.items = self.items # refrescar, ver de corregir
            self.progreso(len(self.items))
            dialog.alertDialog(self, 'Proceso finalizado OK!\n\nAceptadas: %d\nRechazadas: %d' % (ok, rechazadas), 'Autorizaci�n')
        except SoapFault,e:
            self.error(e.faultcode, e.faultstring.encode("ascii","ignore"))
        except wsfe.WSFEError,e:
            self.error(e.code, e.msg.encode("ascii","ignore"))
        except Exception, e:
            self.error('Excepci�n',unicode(e))

    def on_btnAutorizarLote_mouseClick(self, event):
        try:
            ok = 0
            rechazadas = 0
            cols = self.cols
            items = []
            self.progreso(0)
            cbt_desde = cbt_hasta = None
            datos = {
                'tipo_cbte': None,
                'punto_vta': None,
                'fecha_cbte': None,
                'fecha_venc_pago': None,
                'fecha_cbte': None,
                'fecha_venc_pago': None,
                'fecha_serv_desde': None,
                'fecha_serv_hasta': None,
                'id': None,
            }
            importes = {
                'imp_total': Decimal(0),
                'imp_tot_conc': Decimal(0),
                'imp_neto': Decimal(0),
                'impto_liq':Decimal(0),
                'impto_liq_rni': Decimal(0),
                'imp_op_ex': Decimal(0),
            }
            for i, item in self.get_selected_items():
                if cbt_desde is None or int(item['cbt_numero']) < cbt_desde:
                    cbt_desde = int(item['cbt_numero'])
                if cbt_hasta is None or int(item['cbt_numero']) > cbt_hasta:
                    cbt_hasta = int(item['cbt_numero'])
                for key in item:
                    if key in datos:
                        if datos[key] is None:
                            datos[key] = item[key]
                        elif datos[key] != item[key]:
                            raise RuntimeError(u"%s tiene valores distintos en el lote!" % key)
                    if key in importes:
                        importes[key] = importes[key] + Decimal(str(item[key]).replace(",","."))
                
            kargs = {'cbt_desde': cbt_desde, 'cbt_hasta': cbt_hasta}
            kargs.update({'tipo_doc': 99, 'nro_doc':  '0'})
            kargs.update(datos)
            kargs.update(importes)
            if kargs['fecha_serv_desde'] and kargs['fecha_serv_hasta']:
                kargs['presta_serv'] = 1
            else:
                kargs['presta_serv'] = 0
                del kargs['fecha_serv_desde'] 
                del kargs['fecha_serv_hasta']
            
            if 'id' not in kargs or kargs['id'] == "":
                id = long(kargs['cbt_desde'])
                id += (int(kargs['tipo_cbte'])*10**4 + int(kargs['punto_vta']))*10**8
                kargs['id'] = id
            
            if DEBUG:
                self.log('\n'.join(["%s='%s'" % (k,v) for k,v in kargs.items()]))
            
            if dialog.messageDialog(self, "Confirma Lote:\n"
                "Tipo: %(tipo_cbte)s Desde: %(cbt_desde)s Hasta %(cbt_hasta)s\n"
                "Neto: %(imp_neto)s IVA: %(impto_liq)s Total: %(imp_total)s" 
                % kargs, "Autorizar lote:").accepted:
                ret = wsfe.aut(self.client, self.token, self.sign, cuit, **kargs)
                kargs.update(ret)
            
                for i, item in self.get_selected_items():
                    for key in ret:
                        item[key] = ret[key]
                    item['id'] = kargs['id']
                    
                self.log("ID: %s CAE: %s Motivo: %s Reproceso: %s" % (kargs['id'], kargs['cae'], kargs['motivo'],kargs['reproceso']))
                if kargs['resultado'] == "R":
                    rechazadas += 1
                else:
                    ok += 1

                self.items = self.items # refrescar, ver de corregir
                self.progreso(len(self.items))
                dialog.alertDialog(self, 'Proceso finalizado OK!\n\nAceptadas: %d\nRechazadas: %d' % (ok, rechazadas), 'Autorizaci�n')
        except SoapFault,e:
            self.log(self.client.xml_request)
            self.log(self.client.xml_response)
            self.error(e.faultcode, e.faultstring.encode("ascii","ignore"))
        except wsfe.WSFEError,e:
            self.error(e.code, e.msg.encode("ascii","ignore"))
        except Exception, e:
            self.error('Excepci�n',unicode(e))

    def on_btnPrevisualizar_mouseClick(self, event):
        try:
            for i, item in self.get_selected_items():
                archivo = self.generar_factura(item)
                os.system(archivo)
        except Exception, e:
            self.error('Excepci�n',unicode(e))

    def on_btnGenerar_mouseClick(self, event):
        for item in self.items:
            archivo = self.generar_factura(item)

    def on_btnEnviar_mouseClick(self, event):
        try:
            ok = no = 0
            self.progreso(0)
            for i, item in self.get_selected_items():
                if not item['cae'] in ("", "NULL"):
                    archivo = self.generar_factura(item)
                    self.enviar_mail(item,archivo)
                    ok += 1
                else:
                    self.log("No se envia factura %s por no tener CAE" % item['cbt_numero'])
                    no += 1
                self.progreso(i)
            self.progreso(len(self.items))
            dialog.alertDialog(self, 'Proceso finalizado OK!\n\nEnviados: %d\nNo enviados: %d' % (ok, no), 'Envio de Email')
        except Exception, e:
            self.error('Excepci�n',unicode(e))
            
    def generar_factura(self, item):
        fmtdate = lambda d: len(d)==8 and "%s/%s/%s" % (d[6:8], d[4:6], d[0:4]) or ''
        fmtimp = lambda i: ("%0.2f" % Decimal(str(i).replace(",","."))).replace(".",",")
        fmtcuit = lambda c: len(c)==11 and "%s-%s-%s" % (c[0:2], c[2:10], c[10:])
        
        f = Form(conf_fact.get('formato','factura.csv'))
        for k,v in conf_pdf.items():
            f.set(k,v)

        numero = "%04d-%08d" % (int(item['punto_vta']), int(item['cbt_numero']))
        f.set('Numero', numero)
        f.set('Fecha', fmtdate(item['fecha_cbte']))
        f.set('Vencimiento', fmtdate(item['fecha_venc_pago']))
        
        if int(item['tipo_cbte']) in (1, 2, 3, 4, 5, 39, 60, 63):
            letra = "A"
        else:
            letra = "B"
        f.set('LETRA', letra)
        f.set('TipoCBTE', "COD.%02d" % int(item['tipo_cbte']))

        tipos = { (1, 6): 'Factura', (2, 7): 'Nota de D�bito', 
            (3, 8): 'Nota de Cr�dito',
            (4, 9): 'Recibo', (10,): 'Notas de Venta al contado', 
            (60, 61): 'Cuenta de Venta y L�quido producto',
            (63, 64): 'Liquidaci�n',
            (39, 40): '???? (R.G. N� 3419)'}

        tipo = ""
        for k,v in tipos.items():
            if int(int(item['tipo_cbte'])) in k:
                tipo = v
        f.set('Comprobante.L', tipo)

        f.set('Periodo.Desde', fmtdate(item['fecha_serv_desde']))
        f.set('Periodo.Hasta', fmtdate(item['fecha_serv_hasta']))
        
        f.set('Cliente.Nombre', item['nombre'])
        f.set('Cliente.Domicilio', item['domicilio'])
        f.set('Cliente.Localidad', item['localidad'])
        if 'provincia' in item:
            f.set('Cliente.Provincia', item['provincia'])
        f.set('Cliente.Telefono', item['telefono'])
        f.set('Cliente.IVA', item['categoria'])
        f.set('Cliente.CUIT', fmtcuit(item['nro_doc']))
        if 'cliente.observaciones' in item:
            f.set('Cliente.Observaciones', item['cliente.observaciones'])

        li = 1
        for i in range(25):
            if 'cantidad%d' % i in item:
                f.set('Item.Cantidad%02d' % i, item['cantidad%d' % i])
            if 'descripcion%d' % i in item:
                f.set('Item.Descripcion%02d' % i, item['descripcion%d' % i])
                li = i
            if 'importe%d' % i in item:
                f.set('Item.Importe%02d' % i, fmtimp(item['importe%d' % i]))
                li = 0
        if li:
            f.set('Item.Importe%02d' % li, fmtimp(item['imp_neto']))

        if 'observaciones' in item:
            f.set('Observaciones', item['observaciones'])
        
        f.set('NETO', fmtimp(item['imp_neto']))
        f.set('IVA21', fmtimp(item['impto_liq']))
        f.set('TOTAL', fmtimp(item['imp_total']))

        f.set('CAE', item['cae'])
        f.set('CAE.Vencimiento', fmtdate(item['fecha_vto']))
        if item['cae']!="NULL":
            barras = ''.join([cuit, item['tipo_cbte'], item['punto_vta'], 
                item['cae'], item['fecha_vto']])
            barras = barras + digito_verificador_modulo10(barras)
        else:
            barras = ""

        f.set('CodigoBarras', barras)
        f.set('CodigoBarrasLegible', barras)

        d = os.path.join(conf_fact.get('directorio', "."), item['fecha_cbte'])
        if not os.path.isdir(d):
            os.mkdir(d)
        fs = conf_fact.get('archivo','numero').split(",")
        it = item.copy()
        it['numero'] = numero
        it['mes'] = item['fecha_cbte'][4:6]
        it['a�o'] = item['fecha_cbte'][0:4]
        fn = ''.join([str(it.get(ff,ff)) for ff in fs])
        archivo = os.path.join(d, "%s.pdf" % fn)
        f.render(archivo)
        return archivo
    
    def enviar_mail(self, item, archivo):
        archivo = self.generar_factura(item)
        if item['email']:
            msg = MIMEMultipart()
            msg['Subject'] = conf_mail['motivo'].replace("NUMERO",item['cbt_numero'])
            msg['From'] = conf_mail['remitente']
            msg['Reply-to'] = msg['From']
            msg['To'] = item['email']
            msg.preamble = 'Mensaje de multiples partes.\n'
            
            part = MIMEText(conf_mail['cuerpo'])
            msg.attach(part)
            
            part = MIMEApplication(open(archivo,"rb").read())
            part.add_header('Content-Disposition', 'attachment', filename=os.path.basename(archivo))
            msg.attach(part)

            try:
                self.log("Enviando email: %s a %s" % (msg['Subject'], msg['To']))
                if not self.smtp:
                    self.smtp = SMTP(conf_mail['servidor'])
                    if conf_mail['usuario'] and conf_mail['clave']:
                        self.smtp.ehlo()
                        self.smtp.login(conf_mail['usuario'], conf_mail['clave'])
                self.smtp.sendmail(msg['From'], msg['To'], msg.as_string())
            except Exception,e:
                self.error('Excepci�n',unicode(e))
            
        
if __name__ == '__main__':
    if len(sys.argv)>1:
        CONFIG_FILE = sys.argv[1]
    config = SafeConfigParser()
    config.read(CONFIG_FILE)
    cert = config.get('WSAA','CERT')
    privatekey = config.get('WSAA','PRIVATEKEY')
    cuit = config.get('WSFE','CUIT')
    if config.has_option('WSFE','ENTRADA'):
        entrada = config.get('WSFE','ENTRADA')
    else:
        entrada = "facturas.csv"
    if config.has_option('WSFE','ENTRADA'):
        salida = config.get('WSFE','SALIDA')
    else:
        salida = "resultado.csv"
    
    if config.has_section('FACTURA'):
        conf_fact = dict(config.items('FACTURA'))
    else:
        conf_fact = {}
    
    conf_pdf = dict(config.items('PDF'))
    conf_mail = dict(config.items('MAIL'))
      
    if config.has_option('WSAA','URL') and not HOMO:
        wsaa_url = config.get('WSAA','URL')
    else:
        wsaa_url = wsaa.WSAAURL
    if config.has_option('WSFE','URL') and not HOMO:
        wsfe_url = config.get('WSFE','URL')
    else:
        wsfe_url = wsfe.WSFEURL

    app = model.Application(PyRece)
    app.MainLoop()
