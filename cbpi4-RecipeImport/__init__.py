
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
import base64

logger = logging.getLogger(__name__)


class RecipeCreation(CBPiExtension):
    def __init__(self, cbpi):
        self.cbpi = cbpi
        path = os.path.dirname(__file__)
        self.cbpi.register(self, "/creation")
        self._task = asyncio.create_task(self.run())


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
        TEMP_UNIT = self.cbpi.config.get("TEMP_UNIT", "C")
        self.default_boil_temp = 99 if TEMP_UNIT == "C" else 212
        self.default_cool_temp = 20 if TEMP_UNIT == "C" else 68 

        if boil_temp is None:
            logger.info("INIT Boil Temp Setting")
            try:
                await self.cbpi.config.add("steps_boil_temp", default_boil_temp, ConfigType.NUMBER, "Default Boil Temperature for Recipe Creation")
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
                await self.cbpi.config.add("steps_cooldown_temp", default_cool_temp, ConfigType.NUMBER, "Cooldown temp will send notification when this temeprature is reached")
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

    @request_mapping(path='/kbh', method="POST", auth_required=False)
    async def create_kbh_recipe(self, request): 
        kbh_id = await request.json()
        await self.kbh_recipe_creation(kbh_id['id'])
        return web.Response(status=200)

    @request_mapping(path='/xml', method="POST", auth_required=False)
    async def create_xml_recipe(self, request): 
        xml_id = await request.json()
        await self.xml_recipe_creation(xml_id['id'])
        return web.Response(status=200)

    @request_mapping(path='/bf', method="POST", auth_required=False)
    async def create_bf_recipe(self, request): 
        bf_id = await request.json()
        await self.bf_recipe_creation(bf_id['id'])
        return web.Response(status=200)

    async def kbh_recipe_creation(self, Recipe_ID):
        config = self.get_config_values()
        if self.kettle is not None:
            # load beerxml file located in upload folder
            self.path = os.path.join(".", 'config', "upload", "kbh.db")
            if os.path.exists(self.path) is False:
                self.cbpi.notify("File Not Found", "Please upload a kbh V2 databsel file", NotificationType.ERROR)
                
            try:
                # Get Recipe Nmae
                conn = sqlite3.connect(self.path)
                c = conn.cursor()
                c.execute('SELECT Sudname FROM Sud WHERE ID = ?', (Recipe_ID,))
                row = c.fetchone()
                name = row[0]

                # get MashIn Temp
                mashin_temp = None
                c.execute('SELECT Temp FROM Rasten WHERE Typ = 0 AND SudID = ?', (Recipe_ID,))
                row = c.fetchone()
                try:
                    if self.cbpi.config.get("TEMP_UNIT", "C") == "C":
                        mashin_temp = str(int(row[0]))
                    else:
                        mashin_temp = str(round(9.0 / 5.0 * int(row[0]) + 32))
                except:
                    pass

                # get the hop addition times
                c.execute('SELECT Zeit FROM Hopfengaben WHERE Vorderwuerze = 0 AND SudID = ?', (Recipe_ID,))
                hops = c.fetchall()

                # get the misc addition times
                c.execute('SELECT Zugabedauer FROM WeitereZutatenGaben WHERE Zeitpunkt = 1 AND SudID = ?', (Recipe_ID,))
                miscs = c.fetchall()

                try:
                    c.execute('SELECT Zeit FROM Hopfengaben WHERE Vorderwuerze = 1 AND SudID = ?', (Recipe_ID,))
                    FW_Hops = c.fetchall()
                    FirstWort = self.getFirstWort(FW_Hops,"kbh")
                except:
                    FirstWort = "No"

                c.execute('SELECT Kochdauer FROM Sud WHERE ID = ?', (Recipe_ID,))
                row = c.fetchone()
                BoilTime = str(int(row[0]))



                await self.create_recipe(name)

                if mashin_temp is not None:
                    step_type = self.mashin if self.mashin != "" else "MashInStep"
                    step_string = { "name": "MashIn",
                                    "props": {
                                        "AutoMode": self.AutoMode,
                                        "Kettle": self.id,
                                        "Sensor": self.kettle.sensor,
                                        "Temp": mashin_temp,
                                        "Timer": "0",
                                        "Notification": "Target temperature reached. Please add malt."
                                        },
                                    "status_text": "",
                                    "status": "I",
                                    "type": step_type
                                    }
                    await self.create_step(step_string)

                for row in c.execute('SELECT Name, Temp, Dauer FROM Rasten WHERE Typ <> 0 AND SudID = ?', (Recipe_ID,)):
                    step_type = self.mash if self.mash != "" else "MashStep"
                    step_string = { "name": str(row[0]),
                                    "props": {
                                        "AutoMode": self.AutoMode,
                                        "Kettle": self.id,
                                        "Sensor": self.kettle.sensor,
                                        "Temp": str(int(row[1])) if self.TEMP_UNIT == "C" else str(round(9.0 / 5.0 * int(row[1]) + 32)),
                                        "Timer": str(int(row[2]))
                                        },
                                    "status_text": "",
                                    "status": "I",
                                    "type": step_type
                                    }
                    await self.create_step(step_string)

                # MashOut -> Notification step that sends notification and waits for user input to move to next step (AutoNext=No)
                if self.mashout == "NotificationStep":
                    step_string = { "name": "Lautering",
                                    "props": {
                                        "AutoNext": "No",
                                        "Kettle": self.id,
                                        "Notification": "Mash Process completed. Please start lautering and press next to start boil."

                                        },
                                    "status_text": "",
                                    "status": "I",
                                    "type": self.mashout
                                    }
                    await self.create_step(step_string)


                Hops = self.getBoilAlerts(hops, miscs, "kbh")
                step_type = self.boil if self.boil != "" else "BoilStep"
                step_string = { "name": "Boil Step",
                            "props": {
                                "AutoMode": self.AutoMode,
                                "Kettle": self.id,
                                "Sensor": self.kettle.sensor,
                                "Temp": int(self.BoilTemp),
                                "Timer": BoilTime,
                                "First_Wort": FirstWort,
                                "LidAlert": "Yes",
                                "Hop_1": Hops[0],
                                "Hop_2": Hops[1],
                                "Hop_3": Hops[2],
                                "Hop_4": Hops[3],
                                "Hop_5": Hops[4],
                                "Hop_6": Hops[5]
                                },
                            "status_text": "",
                            "status": "I",
                            "type": step_type 
                        }

                await self.create_step(step_string)

                await self.create_Whirlpool_Cooldown()
 
                self.cbpi.notify('KBH Recipe created', name, NotificationType.INFO)

            except:
                self.cbpi.notify('KBH Recipe creation failure', name, NotificationType.ERROR)
                pass
        else:
            self.cbpi.notify('Recipe Upload', 'No default Kettle defined. Please specify default Kettle in settings', NotificationType.ERROR)

    async def xml_recipe_creation(self, Recipe_ID):
        config = self.get_config_values()

        if self.kettle is not None:
            # load beerxml file located in upload folder
            self.path = os.path.join(".", 'config', "upload", "beer.xml")
            if os.path.exists(self.path) is False:
                self.cbpi.notify("File Not Found", "Please upload a Beer.xml File", NotificationType.ERROR)


            e = xml.etree.ElementTree.parse(self.path).getroot()
            recipe = e.find('./RECIPE[%s]' % (str(Recipe_ID)))
            hops = recipe.findall('./HOPS/HOP')
            miscs = recipe.findall('MISCS/MISC[USE="Boil"]')
            name = e.find('./RECIPE[%s]/NAME' % (str(Recipe_ID))).text
            boil_time = float(e.find('./RECIPE[%s]/BOIL_TIME' % (str(Recipe_ID))).text)
            FirstWort= self.getFirstWort(hops, "xml")

            await self.create_recipe(name)
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
                    step_type = self.mashin if self.mashin != "" else "MashInStep"
                    Notification = "Target temperature reached. Please add malt."
                    MashIn_Flag = False
                else:
                    step_type = self.mash if self.mash != "" else "MashStep"
                    Notification = ""

                step_string = { "name": step_name,
                                "props": {
                                        "AutoMode": self.AutoMode,
                                        "Kettle": self.id,
                                        "Sensor": self.kettle.sensor,
                                        "Temp": step_temp,
                                        "Timer": step_timer,
                                        "Notification": Notification
                                        },
                                "status_text": "",
                                "status": "I",
                                "type": step_type
                                }

                await self.create_step(step_string)

            # MashOut -> Simple step that sends notification and waits for user input to move to next step (AutoNext=No)
            if self.mashout == "NotificationStep":
                step_string = { "name": "Lautering",
                                "props": {
                                        "AutoNext": "No",
                                        "Kettle": self.id,
                                        "Notification": "Mash Process completed. Please start lautering and press next to start boil."
                                        },
                                    "status_text": "",
                                    "status": "I",
                                    "type": self.mashout
                                    }
                await self.create_step(step_string)               
                
            # Boil step including hop alarms and alarm for first wort hops -> Automode is set tu yes
            Hops = self.getBoilAlerts(hops, miscs, "xml")
            step_kettle = self.id
            step_type = self.boil if self.boil != "" else "BoilStep"
            step_time = str(int(boil_time))
            step_temp = self.BoilTemp
            sensor = self.kettle.sensor
            LidAlert = "Yes"

            step_string = { "name": "Boil Step",
                            "props": {
                                "AutoMode": self.AutoMode,
                                "Kettle": step_kettle,
                                "Sensor": sensor,
                                "Temp": step_temp,
                                "Timer": step_time,
                                "First_Wort": FirstWort,
                                "LidAlert": LidAlert,
                                "Hop_1": Hops[0],
                                "Hop_2": Hops[1],
                                "Hop_3": Hops[2],
                                "Hop_4": Hops[3],
                                "Hop_5": Hops[4],
                                "Hop_6": Hops[5]
                                },
                            "status_text": "",
                            "status": "I",
                            "type": step_type 
                        }

            await self.create_step(step_string)

            await self.create_Whirlpool_Cooldown()

            self.cbpi.notify('BeerXML Recipe created ', name, NotificationType.INFO)
        else:
            self.cbpi.notify('Recipe Upload', 'No default Kettle defined. Please specify default Kettle in settings', NotificationType.ERROR)


   # XML functions to retrieve xml repice parameters (if multiple recipes are stored in one xml file, id could be used)

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

    async def bf_recipe_creation(self, Recipe_ID):
        config = self.get_config_values()

        if self.kettle is not None:

            brewfather = True
            result=[]
            self.bf_url="https://api.brewfather.app/v1/recipes/" + Recipe_ID
            brewfather_user_id = self.cbpi.config.get("brewfather_user_id", None)
            if brewfather_user_id == "" or brewfather_user_id is None:
                brewfather = False

            brewfather_api_key = self.cbpi.config.get("brewfather_api_key", None)
            if brewfather_api_key == "" or brewfather_api_key is None:
                brewfather = False
            if brewfather == True:
                encodedData = base64.b64encode(bytes(f"{brewfather_user_id}:{brewfather_api_key}", "ISO-8859-1")).decode("ascii")
                headers={"Authorization": "Basic %s" % encodedData}
                bf_recipe = ""
                logging.info(headers)
                async with aiohttp.ClientSession(headers=headers) as bf_session:
                    async with bf_session.get(self.bf_url) as r:
                        bf_recipe = await r.json()
                    await bf_session.close()


            if bf_recipe !="":
                RecipeName = bf_recipe['name']
                BoilTime = bf_recipe['boilTime']
                mash_steps=bf_recipe['mash']['steps']
                hops=bf_recipe['hops']
                try:
                    miscs = bf_recipe['miscs']
                except:
                    miscs = None

                FirstWort = self.getFirstWort(hops, "bf")

                await self.create_recipe(RecipeName)

                # Mash Steps -> first step is different as it heats up to defined temp and stops with notification to add malt
                # AutoMode is yes to start and stop automatic mode or each step
                MashIn_Flag = True
                step_kettle = self.id
                for step in mash_steps:
                    try:
                        step_name = step['name']
                        if step_name == "":
                            step_name = "MashStep" 
                    except:
                        step_name = "MashStep"
                    step_timer = str(int(step['stepTime']))

                    if self.TEMP_UNIT == "C":
                        step_temp = str(int(step['stepTemp']))
                    else:
                        step_temp = str(round((9.0 / 5.0 * int(step['stepTemp']) + 32)))

                    sensor = self.kettle.sensor
                    if MashIn_Flag == True and int(step_timer) == 0:
                        step_type = self.mashin if self.mashin != "" else "MashInStep"
                        Notification = "Target temperature reached. Please add malt."
                        MashIn_Flag = False
                    else:
                        step_type = self.mash if self.mash != "" else "MashStep"
                        Notification = ""

                    step_string = { "name": step_name,
                                    "props": {
                                        "AutoMode": self.AutoMode,
                                        "Kettle": self.id,
                                        "Sensor": self.kettle.sensor,
                                        "Temp": step_temp,
                                        "Timer": step_timer,
                                        "Notification": Notification
                                        },
                                     "status_text": "",
                                     "status": "I",
                                     "type": step_type
                                    }
                    await self.create_step(step_string)

                # MashOut -> Simple step that sends notification and waits for user input to move to next step (AutoNext=No)

                if self.mashout == "NotificationStep":
                    step_string = { "name": "Lautering",
                                    "props": {
                                        "AutoNext": "No",
                                        "Kettle": self.id,
                                        "Notification": "Mash Process completed. Please start lautering and press next to start boil."
                                        },
                                    "status_text": "",
                                    "status": "I",
                                    "type": self.mashout
                                    }
                await self.create_step(step_string)    
                # Boil step including hop alarms and alarm for first wort hops -> Automode is set tu yes
                Hops = self.getBoilAlerts(hops , miscs, "bf")
                logging.info(Hops)

                step_kettle = self.id
                step_time = str(int(BoilTime))
                step_type = self.boil if self.boil != "" else "BoilStep"
                step_temp = self.BoilTemp
                sensor = self.kettle.sensor
                LidAlert = "Yes"

                step_string = { "name": "Boil Step",
                            "props": {
                                "AutoMode": self.AutoMode,
                                "Kettle": step_kettle,
                                "Sensor": sensor,
                                "Temp": step_temp,
                                "Timer": step_time,
                                "First_Wort": FirstWort,
                                "LidAlert": LidAlert,
                                "Hop_1": Hops[0],
                                "Hop_2": Hops[1],
                                "Hop_3": Hops[2],
                                "Hop_4": Hops[3],
                                "Hop_5": Hops[4],
                                "Hop_6": Hops[5]
                                },
                            "status_text": "",
                            "status": "I",
                            "type": step_type 
                        }

                await self.create_step(step_string)

                await self.create_Whirlpool_Cooldown()

                self.cbpi.notify('Brewfather App Recipe created: ', RecipeName, NotificationType.INFO)
        else:
            self.cbpi.notify('Recipe Upload', 'No default Kettle defined. Please specify default Kettle in settings', NotificationType.ERROR)

    def getBoilAlerts(self, hops, miscs, recipe_type):
        alerts = []
        for hop in hops:
            if recipe_type == "xml":
                use = hop.find('USE').text
                ## Hops which are not used in the boil step should not cause alerts
                if use != 'Aroma' and use != 'Boil':
                    continue
                alerts.append(float(hop.find('TIME').text))
            elif recipe_type == "bf":
                use = hop['use']
                if use != 'Aroma' and use != 'Boil':
                    continue
                alerts.append(float(hop['time']))
            elif recipe_type == "kbh":
                alerts.append(float(hop[0]))
        ## There might also be miscelaneous additions during boild time
        if miscs is not None:
            for misc in miscs:
                if recipe_type == "xml":
                    alerts.append(float(misc.find('TIME').text))
                elif recipe_type == "bf":
                    use = misc['use']
                    if use != 'Aroma' and use != 'Boil':
                        continue
                    alerts.append(float(misc['time']))
                elif recipe_type == "kbh":
                    alerts.append(float(misc[0]))
        ## Dedupe and order the additions by their time, to prevent multiple alerts at the same time
        alerts = sorted(list(set(alerts)))
        ## CBP should have these additions in reverse
        alerts.reverse()
        hop_alerts = []
        for i in range(0,6):
            try:
                hop_alerts.append(str(int(alerts[i])))
            except:
                hop_alerts.append(None)
        return hop_alerts

    def getFirstWort(self, hops, recipe_type):
        alert = "No"
        if recipe_type == "kbh":
            if len(hops) != 0:
                alert = "Yes"
        elif recipe_type == "xml":
            for hop in hops:
                use = hop.find('USE').text
                ## Hops which are not used in the boil step should not cause alerts
                if use != 'First Wort':
                    continue
                alert = "Yes"
        elif recipe_type == "bf":
            for hop in hops:
                if hop['use'] == "First Wort":
                    alert="Yes"
        return alert

    async def create_Whirlpool_Cooldown(self):
        # Add Waitstep as Whirlpool
        if self.cooldown != "WaiStep" and self.cooldown !="":
            step_string = { "name": "Whirlpool",
                            "props": {
                                "Kettle": self.id,
                                "Timer": "15"
                                },
                            "status_text": "",
                            "status": "I",
                            "type": "WaitStep" 
                        }
            await self.create_step(step_string)

        # CoolDown step is sending a notification when cooldowntemp is reached
        step_type = self.cooldown if self.cooldown != "" else "WaitStep"
        step_name = "CoolDown"
        cooldown_sensor = ""
        step_temp = ""
        step_timer = "15"
        if step_type == "CooldownStep":
            cooldown_sensor = self.cbpi.config.get("steps_cooldown_sensor", None)
            if cooldown_sensor is None or cooldown_sensor == '':
                cooldown_sensor = self.kettle.sensor  # fall back to kettle sensor if no other sensor is specified
            step_timer = ""                
            step_temp = int(self.CoolDownTemp)
            step_string = { "name": "Cooldown",
                            "props": {
                                "Kettle": self.id,
                                "Timer": step_timer,
                                "Temp": step_temp,
                                "Sensor": cooldown_sensor
                                },
                            "status_text": "",
                            "status": "I",
                            "type": step_type 
                        }
            await self.create_step(step_string)

    def get_config_values(self):
        self.kettle = None
        #Define MashSteps
        self.TEMP_UNIT = self.cbpi.config.get("TEMP_UNIT", "C")
        self.AutoMode = self.cbpi.config.get("AutoMode", "Yes")
        self.mashin =  self.cbpi.config.get("steps_mashin", "MashInStep")
        self.mash = self.cbpi.config.get("steps_mash", "MashStep") 
        self.mashout = self.cbpi.config.get("steps_mashout", None) # Currently used only for the Braumeister 
        self.boil = self.cbpi.config.get("steps_boil", "BoilStep") 
        self.whirlpool="Waitstep"
        self.cooldown = self.cbpi.config.get("steps_cooldown", "WaitStep") 
        #get default boil temp from settings
        self.BoilTemp = self.cbpi.config.get("steps_boil_temp", 98)
        #get default cooldown temp alarm setting
        self.CoolDownTemp = self.cbpi.config.get("steps_cooldown_temp", 25)
        # get default Kettle from Settings       
        self.id = self.cbpi.config.get('MASH_TUN', None)
        try:
            self.kettle = self.cbpi.kettle.find_by_id(self.id) 
        except:
            self.cbpi.notify('Recipe Upload', 'No default Kettle defined. Please specify default Kettle in settings', NotificationType.ERROR)
        config_values = { "kettle": self.kettle,
                          "kettle_id": str(self.id),
                          "mashin": str(self.mashin),
                          "mash": str(self.mash),
                          "mashout": str(self.mashout),
                          "boil": str(self.boil),
                          "whirlpool": str(self.whirlpool),
                          "cooldown": str(self.cooldown),
                          "boiltemp": str(self.BoilTemp),
                          "cooldowntemp": str(self.CoolDownTemp),
                          "temp_unit": str(self.TEMP_UNIT),
                          "AutoMode": str(self.AutoMode)
                        }
        return config_values

    async def create_recipe(self, name):
        # Create recipe in recipe Book with name of first recipe in xml file
        self.recipeID = await self.cbpi.recipe.create(name)
        # send recipe to mash profile
        await self.cbpi.recipe.brew(self.recipeID)
        # remove empty recipe from recipe book
        await self.cbpi.recipe.remove(self.recipeID)

    # function to create json to be send to api to add a step to the current mash profile. Currently all properties are send to each step which does not cuase an issue
    async def create_step(self, step_string):
        #get server port from settings and define url for api calls -> adding steps
        self.port = str(self.cbpi.static_config.get('port',8000))
        self.url="http://127.0.0.1:" + self.port + "/step2/"
        # convert step:string to json required for api call. 
        step = json.dumps(step_string)
        headers={'Content-Type': 'application/json', 'Accept': 'application/json'}
        async with aiohttp.ClientSession(headers=headers) as session:
            async with session.post(self.url, data=step) as response:
                return await response.text()
            await self.push_update()



def setup(cbpi):
    cbpi.plugin.register("RecipeCreation", RecipeCreation)
    pass
