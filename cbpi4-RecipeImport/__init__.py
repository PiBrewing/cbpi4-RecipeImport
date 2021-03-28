
# -*- coding: utf-8 -*-
import os
import pathlib
import aiohttp
from aiohttp import web
import logging
from unittest.mock import MagicMock, patch
import asyncio
import sqlite3
import random
import shortuuid
from cbpi.api import *
import xml.etree.ElementTree
from voluptuous.schema_builder import message
from cbpi.api.dataclasses import NotificationAction, NotificationType
from cbpi.controller.kettle_controller import KettleController
from cbpi.api.base import CBPiBase
from cbpi.api.config import ConfigType
import json
import webbrowser

logger = logging.getLogger(__name__)

def get_kbh_recipes():
    try:
        path = os.path.join(".", 'config', "upload", "kbh.db")
        conn = sqlite3.connect(path)
        c = conn.cursor()
        c.execute('SELECT ID, Sudname, Status FROM Sud')
        data = c.fetchall()
        result = []
        for row in data:
           result.append(str(row[1])+"."+str(row[0]))
        return result
    except:
        return []

def get_xml_recipes():
    try:
        path = os.path.join(".", 'config', "upload", "beer.xml")
        e = xml.etree.ElementTree.parse(path).getroot()
        result = []
        counter = 1
        for idx, val in enumerate(e.findall('RECIPE')):
            result.append(val.find("NAME").text+"."+str(counter))
            counter +=1
        return result
    except:
        return []

class RecipeImport(CBPiExtension):
    def __init__(self, cbpi):
        self.cbpi = cbpi
        path = os.path.dirname(__file__)
        self.cbpi.register(self, "/cbpi_RecipeImport", static=os.path.join(path, "static"))
        self.cbpi.register(self, "/cbpi_RecipeImport")
        self._task = asyncio.create_task(self.run())

    def allowed_file(self, filename, extension):
        return '.' in filename and filename.rsplit('.', 1)[1] in set([extension])

    @request_mapping(path='/', method="POST", auth_required=False)
    async def RecipeImport(self, request):
        data = await request.post()
        logger.info(data)
        if 'xml_upload' in data:
            try:
                beerxml = data['xml_upload']
                filename = beerxml.filename
                beerxml_file = data['xml_upload'].file
                content = beerxml_file.read().decode()
                if beerxml_file and self.allowed_file(filename, 'xml'):
                    self.path = os.path.join(".", 'config', "upload", "beer.xml")
    
                    f = open(self.path, "w")
                    f.write(content)
                    f.close()
            except: 
                pass

        if 'kbh_upload' in data:
            try:
                kbh = data['kbh_upload']
                filename = kbh.filename
                logger.info(filename)
                kbh_file = kbh.file
                content = kbh_file.read()
                if kbh_file and self.allowed_file(filename, 'sqlite'):
                    self.path = os.path.join(".", 'config', "upload", "kbh.db")

                    f=open(self.path, "wb")
                    f.write(content)
                    f.close()
            except:
                pass
        self.cbpi.plugin.register("RecipeLoad", RecipeLoad)
        return web.HTTPFound('static/index.html')

    async def run(self):
        logger.info('Starting Recipe Import Plugin')
        if os.path.exists(os.path.join(".", 'config', "upload")) is False:
            logger.info("Creating Upload folder")
            pathlib.Path(os.path.join(".", 'config/upload')).mkdir(parents=True, exist_ok=True) 
        await self.RecipeSettings()
        pass

    async def RecipeSettings(self):
        boil_temp = self.cbpi.config.get("steps_boil_temp", None)
        cooldown_sensor = self.cbpi.config.get("steps_cooldown_sensor", None)
        cooldown_temp = self.cbpi.config.get("steps_cooldown_temp", None)
        mashin_step = self.cbpi.config.get("steps_mashin", None)
        mash_step = self.cbpi.config.get("steps_mash", None)
        mashout_step = self.cbpi.config.get("steps_mashout", None)
        boil_step = self.cbpi.config.get("steps_boil", None)
        cooldown_step = self.cbpi.config.get("steps_cooldown", None)

        if boil_temp is None:
            logger.info("INIT Boil Temp Setting")
            try:
                await self.cbpi.config.add("steps_boil_temp", "98", ConfigType.NUMBER, "Default Boil Temperature for Recipe Creation")
            except:
                logger.warning('Unable to update database')

        if cooldown_sensor is None:
            logger.info("INIT Cooldown Sensor Setting")
            try:
                await self.cbpi.config.add("steps_cooldown_sensor", "", ConfigType.SENSOR, "Alternative Sensor to monitor temperature durring cooldown (if not selected, Kettle Sensor will be used)")
            except:
                logger.warning('Unable to update database')

        if cooldown_temp is None:
            logger.info("INIT Cooldown Temp Setting")
            try:
                await self.cbpi.config.add("steps_cooldown_temp", "25", ConfigType.NUMBER, "Cooldown temp will send notification when this temeprature is reached")
            except:
                logger.warning('Unable to update database')

        if cooldown_step is None:
            logger.info("INIT Cooldown Step Type")
            try:
                await self.cbpi.config.add("steps_cooldown", "", ConfigType.STEP, "Cooldown step type")
            except:
                logger.warning('Unable to update database')

        if mashin_step is None:
            logger.info("INIT MashIn Step Type")
            try:
                await self.cbpi.config.add("steps_mashin", "", ConfigType.STEP, "MashIn step type")
            except:
                logger.warning('Unable to update database')

        if mash_step is None:
            logger.info("INIT Mash Step Type")
            try:
                await self.cbpi.config.add("steps_mash", "", ConfigType.STEP, "Mash step type")
            except:
                logger.warning('Unable to update database')

        if mashout_step is None:
            logger.info("INIT MashOut Step Type")
            try:
                await self.cbpi.config.add("steps_mashout", "", ConfigType.STEP, "MashOut step type")
            except:
                logger.warning('Unable to update database')

        if boil_step is None:
            logger.info("INIT Boil Step Type")
            try:
                await self.cbpi.config.add("steps_boil", "", ConfigType.STEP, "Boil step type")
            except:
                logger.warning('Unable to update database')

@parameters([])
class RecipeLoad(CBPiActor):

    def __init__(self, cbpi, id, props):
        super().__init__(cbpi, id, props)

    @action("Create Recipe from KBH databse", parameters=[Property.Select("Recipe",options=get_kbh_recipes())])
    async def load_kbh(self, Recipe, **kwargs):
        self.kettle = None
        Recipe_ID = (Recipe.rsplit(".",1)[1])

        #Define MashSteps
        self.mashin =  self.cbpi.config.get("steps_mashin", "MashStep")
        self.mash = self.cbpi.config.get("steps_mash", "MashStep") 
        self.mashout = self.cbpi.config.get("steps_mashout", None) # Currently used only for the Braumeister 
        self.boil = self.cbpi.config.get("steps_boil", "BoilStep") 
        self.cooldown = self.cbpi.config.get("steps_cooldown", "WaitStep") 
        
        #get default boil temp from settings
        self.BoilTemp = self.cbpi.config.get("steps_boil_temp", 98)

        #get default cooldown temp alarm setting
        self.CoolDownTemp = self.cbpi.config.get("steps_cooldown_temp", 25)

        #get server port from settings and define url for api calls -> adding steps
        self.port = str(self.cbpi.static_config.get('port',8000))
        self.url="http://127.0.0.1:" + self.port + "/step2/"


        # get default Kettle from Settings       
        self.id = self.cbpi.config.get('MASH_TUN', None)
        try:
            self.kettle = self.cbpi.kettle.find_by_id(self.id) 
        except:
            self.cbpi.notify('Recipe Upload', 'No default Kettle defined. Please specify default Kettle in settings', NotificationType.ERROR)
        if self.id is not None or self.id != '':
            # load beerxml file located in upload folder
            self.path = os.path.join(".", 'config', "upload", "kbh.db")
            if os.path.exists(self.path) is False:
                self.cbpi.notify("File Not Found", "Please upload a kbh V2 databsel file", NotificationType.ERROR)
                
            try:
                conn = sqlite3.connect(self.path)
                c = conn.cursor()
                c.execute('SELECT Sudname FROM Sud WHERE ID = ?', (Recipe_ID,))
                row = c.fetchone()
                name = row[0]

                # Create recipe in recipe Book with name of first recipe in xml file
                self.recipeID = await self.cbpi.recipe.create(name)

                # send recipe to mash profile
                await self.cbpi.recipe.brew(self.recipeID)
    
                # remove empty recipe from recipe book
                await self.cbpi.recipe.remove(self.recipeID)
                
                #MashIn Step
                c.execute('SELECT Temp FROM Rasten WHERE Typ = 0 AND SudID = ?', (Recipe_ID,))
                row = c.fetchone()

                step_kettle = self.id
                step_name = "MashIn"
                step_timer = "0"
                step_temp = str(int(row[0]))
                sensor = self.kettle.sensor
                step_type = self.mashin if self.mashin != "" else "MashStep"
                AutoMode = "Yes" if step_type == "BM_MashInStep" else "No"
                await self.create_step(step_type, step_name, step_kettle, step_timer, step_temp, AutoMode, sensor)

                for row in c.execute('SELECT Name, Temp, Dauer FROM Rasten WHERE Typ <> 0 AND SudID = ?', (Recipe_ID,)):
                    step_name = str(row[0])
                    step_temp = str(int(row[1]))
                    step_timer = str(int(row[2]))
                    step_type = self.mash if self.mash != "" else "MashStep"
                    AutoMode = "Yes" if step_type == "BM_MashStep" else "No"
                    await self.create_step(step_type, step_name, step_kettle, step_timer, step_temp, AutoMode, sensor)

                # MashOut -> Simple step that sends notification and waits for user input to move to next step (AutoNext=No)
                if self.mashout == "BM_SimpleStep":
                    step_kettle = self.id
                    step_type = self.mashout
                    step_name = "Remove Malt Pipe"
                    step_timer = ""
                    step_temp = ""
                    AutoMode = ""
                    sensor = ""
                    Notification = "Mash Process completed. Please remove malt pipe and press next to start boil!"
                    AutoNext = "No"
                    await self.create_step(step_type, step_name, step_kettle, step_timer, step_temp, AutoMode, sensor, Notification, AutoNext)

                c.execute('SELECT Kochdauer FROM Sud WHERE ID = ?', (Recipe_ID,))
                row = c.fetchone()
                step_time = str(int(row[0]))

                FirstWortFlag = self.getFirstWortKBH(Recipe_ID)

                BoilTimeAlerts = self.getBoilAlertsKBH(Recipe_ID)

                step_kettle = self.id
                step_type = self.boil if self.boil != "" else "BoilStep"
                step_name = "Boil Step"
                step_temp = int(self.BoilTemp)
                AutoMode = "Yes" if step_type == "BM_BoilStep" else "No"
                sensor = self.kettle.sensor
                Notification = ""
                AutoNext = ""
                FirstWort = 'Yes' if FirstWortFlag == True else 'No'
                Hop1 = str(int(BoilTimeAlerts[0])) if len(BoilTimeAlerts) >= 1 else None
                Hop2 = str(int(BoilTimeAlerts[1])) if len(BoilTimeAlerts) >= 2 else None
                Hop3 = str(int(BoilTimeAlerts[2])) if len(BoilTimeAlerts) >= 3 else None       
                Hop4 = str(int(BoilTimeAlerts[3])) if len(BoilTimeAlerts) >= 4 else None
                Hop5 = str(int(BoilTimeAlerts[4])) if len(BoilTimeAlerts) >= 5 else None
                Hop6 = str(int(BoilTimeAlerts[5])) if len(BoilTimeAlerts) >= 6 else None

                await self.create_step(step_type, step_name, step_kettle, step_time, step_temp, AutoMode, sensor, Notification, AutoNext, FirstWort, Hop1, Hop2, Hop3, Hop4, Hop5, Hop6)

                # CoolDown step is sending a notification when cooldowntemp is reached
                step_type = self.cooldown if self.cooldown != "" else "WaitStep"
                step_name = "CoolDown"
                cooldown_sensor = ""
                step_timer = "15"
                step_temp = ""
                AutoMode = ""
                if step_type == "BM_Cooldown":
                    cooldown_sensor = self.cbpi.config.get("steps_cooldown_sensor", None)
                    if cooldown_sensor is None or cooldown_sensor == '':
                        cooldown_sensor = self.kettle.sensor  # fall back to kettle sensor if no other sensor is specified
                    step_kettle = self.id
                    step_timer = ""                
                    step_temp = int(self.CoolDownTemp)
            
                await self.create_step(step_type, step_name, step_kettle, step_timer, step_temp, AutoMode, cooldown_sensor)


            except:
                pass

            self.cbpi.notify('KBH Recipe Upload', name, NotificationType.INFO)


    def getFirstWortKBH(self, id):
        alert = False
        try:
            conn = sqlite3.connect(self.path)
            c = conn.cursor()
            c.execute('SELECT Zeit FROM Hopfengaben WHERE Vorderwuerze = 1 AND SudID = ?', (id,))
            row = c.fetchall()
            if len(row) != 0:
                alert = True
        except Exception as e:
            self.cbpi.notify("Failed to load Recipe", e.message, NotificationType.ERROR)
            return ('', 500)
        finally:
            if conn:
                conn.close()

        return alert


    def getBoilAlertsKBH(self, id):
        alerts = []
        try:
            conn = sqlite3.connect(self.path)
            c = conn.cursor()
            # get the hop addition times
            c.execute('SELECT Zeit FROM Hopfengaben WHERE Vorderwuerze = 0 AND SudID = ?', (id,))
            rows = c.fetchall()
            
            for row in rows:
                alerts.append(float(row[0]))
                
            # get any misc additions if available
            c.execute('SELECT Zugabedauer FROM WeitereZutatenGaben WHERE Zeitpunkt = 1 AND SudID = ?', (id,))
            rows = c.fetchall()
            
            for row in rows:
                alerts.append(float(row[0]))
                
            ## Dedupe and order the additions by their time, to prevent multiple alerts at the same time
            alerts = sorted(list(set(alerts)))
            
            ## CBP should have these additions in reverse
            alerts.reverse()
        
        except Exception as e:
            self.cbpi.notify("Failed to load Recipe", e.message, NotificationType.ERROR)
            return ('', 500)
        finally:
            if conn:
                conn.close()
                
        return alerts

    @action("Create Recipe from XML File", parameters=[Property.Select("Recipe",options=get_xml_recipes())])
    async def load_xml(self, Recipe, **kwargs):
        self.kettle = None
        Recipe_ID = int(Recipe.rsplit(".",1)[1])
        
        #Define MashSteps
        self.mashin =  self.cbpi.config.get("steps_mashin", "MashStep")
        self.mash = self.cbpi.config.get("steps_mash", "MashStep") 
        self.mashout = self.cbpi.config.get("steps_mashout", None) # Currently used only for the Braumeister 
        self.boil = self.cbpi.config.get("steps_boil", "BoilStep") 
        self.cooldown = self.cbpi.config.get("steps_cooldown", "WaitStep") 
        
        #get default boil temp from settings
        self.BoilTemp = self.cbpi.config.get("steps_boil_temp", 98)

        #get default cooldown temp alarm setting
        self.CoolDownTemp = self.cbpi.config.get("steps_cooldown_temp", 25)

        #get server port from settings and define url for api calls -> adding steps
        self.port = str(self.cbpi.static_config.get('port',8000))
        self.url="http://127.0.0.1:" + self.port + "/step2/"


        # get default Kettle from Settings       
        self.id = self.cbpi.config.get('MASH_TUN', None)
        try:
            self.kettle = self.cbpi.kettle.find_by_id(self.id) 
        except:
            self.cbpi.notify('Recipe Upload', 'No default Kettle defined. Please specify default Kettle in settings', NotificationType.ERROR)
        if self.id is not None or self.id != '':
            # load beerxml file located in upload folder
            self.path = os.path.join(".", 'config', "upload", "beer.xml")
            if os.path.exists(self.path) is False:
                self.cbpi.notify("File Not Found", "Please upload a Beer.xml File", NotificationType.ERROR)

            e = xml.etree.ElementTree.parse(self.path).getroot()

            result = []
            for idx, val in enumerate(e.findall('RECIPE')):
                result.append(val.find("NAME").text)

            # Create recipe in recipe Book with name of first recipe in xml file
            self.recipeID = await self.cbpi.recipe.create(self.getRecipeName(Recipe_ID))

            # send recipe to mash profile
            await self.cbpi.recipe.brew(self.recipeID)

            # remove empty recipe from recipe book
            await self.cbpi.recipe.remove(self.recipeID)

            # Mash Steps -> first step is different as it heats up to defined temp and stops with notification to add malt
            # AutoMode is yes to start and stop automatic mode or each step
            MashIn_Flag = True
            step_kettle = self.id
            for row in self.getSteps(Recipe_ID):
                step_name = str(row.get("name"))
                step_timer = str(int(row.get("timer")))
                step_temp = str(int(row.get("temp")))
                sensor = self.kettle.sensor
                if MashIn_Flag == True and row.get("timer") == 0:
                    step_type = self.mashin if self.mashin != "" else "MashStep"
                    AutoMode = "Yes" if step_type == "BM_MashInStep" else "No"
                    MashIn_Flag = False
                else:
                    step_type = self.mash if self.mash != "" else "MashStep"
                    AutoMode = "Yes" if step_type == "BM_MashStep" else "No"

                await self.create_step(step_type, step_name, step_kettle, step_timer, step_temp, AutoMode, sensor)

            # MashOut -> Simple step that sends notification and waits for user input to move to next step (AutoNext=No)
            if self.mashout == "BM_SimpleStep":
                step_kettle = self.id
                step_type = self.mashout
                step_name = "Remove Malt Pipe"
                step_timer = ""
                step_temp = ""
                AutoMode = ""
                sensor = ""
                Notification = "Mash Process completed. Please remove malt pipe and press next to start boil!"
                AutoNext = "No"
                await self.create_step(step_type, step_name, step_kettle, step_timer, step_temp, AutoMode, sensor, Notification, AutoNext)

            # Boil step including hop alarms and alarm for first wort hops -> Automode is set tu yes
            FirstWortFlag = len(str(self.getFirstWortAlert(Recipe_ID)))
            self.BoilTimeAlerts = self.getBoilAlerts(Recipe_ID)

            step_kettle = self.id
            step_time = str(int(self.getBoilTime(Recipe_ID)))
            step_type = self.boil if self.boil != "" else "BoilStep"
            step_name = "Boil Step"
            step_temp = self.BoilTemp
            AutoMode = "Yes" if step_type == "BM_BoilStep" else "No"
            sensor = self.kettle.sensor
            Notification = ""
            AutoNext = ""
            FirstWort = 'Yes' if FirstWortFlag != 0 else 'No'
            Hop1 = str(int(self.BoilTimeAlerts[0])) if len(self.BoilTimeAlerts) >= 1 else None
            Hop2 = str(int(self.BoilTimeAlerts[1])) if len(self.BoilTimeAlerts) >= 2 else None
            Hop3 = str(int(self.BoilTimeAlerts[2])) if len(self.BoilTimeAlerts) >= 3 else None       
            Hop4 = str(int(self.BoilTimeAlerts[3])) if len(self.BoilTimeAlerts) >= 4 else None
            Hop5 = str(int(self.BoilTimeAlerts[4])) if len(self.BoilTimeAlerts) >= 5 else None 
            Hop6 = str(int(self.BoilTimeAlerts[5])) if len(self.BoilTimeAlerts) >= 6 else None


            await self.create_step(step_type, step_name, step_kettle, step_time, step_temp, AutoMode, sensor, Notification, AutoNext, FirstWort, Hop1, Hop2, Hop3, Hop4, Hop5, Hop6)

            # CoolDown step is sending a notification when cooldowntemp is reached
            step_type = self.cooldown if self.cooldown != "" else "WaitStep"
            step_name = "CoolDown"
            cooldown_sensor = ""
            step_timer = "15"
            step_temp = ""
            AutoMode = ""
            if step_type == "BM_Cooldown":
                cooldown_sensor = self.cbpi.config.get("steps_cooldown_sensor", None)
                if cooldown_sensor is None or cooldown_sensor == '':
                    cooldown_sensor = self.kettle.sensor  # fall back to kettle sensor if no other sensor is specified
                step_kettle = self.id
                step_timer = ""                
                step_temp = self.CoolDownTemp
            
            await self.create_step(step_type, step_name, step_kettle, step_timer, step_temp, AutoMode, cooldown_sensor)

            self.cbpi.notify('BeerXML Recipe Upload', result, NotificationType.INFO)
       
    # function to create json to be send to api to add a step to the current mash profile. Currently all properties are send to each step which does not cuase an issue
    async def create_step(self, type, name, kettle, timer, temp, AutoMode, sensor, Notification = "", AutoNext = "", FirstWort = "", Hop1 = "", Hop2 = "", Hop3 = "", Hop4 = "", Hop5 = "", Hop6=""):
        step_string = { "name": name,
                            "props": {
                                "AutoMode": AutoMode,
                                "Kettle": kettle,
                                "Sensor": sensor,
                                "Temp": temp,
                                "Timer": timer,
                                "Notification": Notification,
                                "AutoNext": AutoNext,
                                "First_Wort": FirstWort,
                                "Hop_1": Hop1,
                                "Hop_2": Hop2,
                                "Hop_3": Hop3,
                                "Hop_4": Hop4,
                                "Hop_5": Hop5,
                                "Hop_6": Hop6
                                },
                            "status_text": "",
                            "status": "I",
                            "type": type
                        }
        # convert step:string to json required for api call. 
        step = json.dumps(step_string)
        headers={'Content-Type': 'application/json', 'Accept': 'application/json'}
        async with aiohttp.ClientSession(headers=headers) as session:
            async with session.post(self.url, data=step) as response:
                return await response.text()
                await self.push_update()

    # XML functions to retrieve xml repice parameters (if multiple recipes are stored in one xml file, id could be used)

    def getRecipeName(self, id):
        e = xml.etree.ElementTree.parse(self.path).getroot()
        return e.find('./RECIPE[%s]/NAME' % (str(id))).text

    def getBoilTime(self, id):
        e = xml.etree.ElementTree.parse(self.path).getroot()
        return float(e.find('./RECIPE[%s]/BOIL_TIME' % (str(id))).text)

    def getBoilAlerts(self, id):
        e = xml.etree.ElementTree.parse(self.path).getroot()
        
        recipe = e.find('./RECIPE[%s]' % (str(id)))
        alerts = []
        for e in recipe.findall('./HOPS/HOP'):
            use = e.find('USE').text
            ## Hops which are not used in the boil step should not cause alerts
            if use != 'Aroma' and use != 'Boil':
                continue
            
            alerts.append(float(e.find('TIME').text))
            ## There might also be miscelaneous additions during boild time
        for e in recipe.findall('MISCS/MISC[USE="Boil"]'):
            alerts.append(float(e.find('TIME').text))
            
        ## Dedupe and order the additions by their time, to prevent multiple alerts at the same time
        alerts = sorted(list(set(alerts)))
        ## CBP should have these additions in reverse
        alerts.reverse()
        
        return alerts

    def getFirstWortAlert(self, id):
        e = xml.etree.ElementTree.parse(self.path).getroot()
        recipe = e.find('./RECIPE[%s]' % (str(id)))
        alerts = []
        for e in recipe.findall('./HOPS/HOP'):
            use = e.find('USE').text
            ## Hops which are not used in the boil step should not cause alerts
            if use != 'First Wort':
                continue
            
            alerts.append(float(e.find('TIME').text))
            
        ## Dedupe and order the additions by their time, to prevent multiple alerts at the same time
        alerts = sorted(list(set(alerts)))
        ## CBP should have these additions in reverse
        alerts.reverse()
        
        return alerts

    def getSteps(self, id):
        e = xml.etree.ElementTree.parse(self.path).getroot()
        steps = []
        for e in e.findall('./RECIPE[%s]/MASH/MASH_STEPS/MASH_STEP' % (str(id))):
            if self.cbpi.config.get("TEMP_UNIT", "C") == "C":
                temp = float(e.find("STEP_TEMP").text)
            else:
                temp = round(9.0 / 5.0 * float(e.find("STEP_TEMP").text) + 32, 2)
            steps.append({"name": e.find("NAME").text, "temp": temp, "timer": float(e.find("STEP_TIME").text)})
            
        return steps

    # these fucntions are not really used as the actor is somehow forced to support other functions via the actions

    async def start(self):
        await super().start()

    async def on(self, power=0):
        self.state = True

    async def off(self):
        self.state = False

    def get_state(self):
        return self.state
    
    async def run(self):
        pass

def setup(cbpi):
    cbpi.plugin.register("RecipeImport", RecipeImport)
    cbpi.plugin.register("RecipeLoad", RecipeLoad)
    pass
