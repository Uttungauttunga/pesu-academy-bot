import datetime
import logging
import re
import traceback
from pathlib import Path
from typing import Optional

import discord
import pytz
import requests_html
from bs4 import BeautifulSoup
from discord import app_commands
from discord.ext import commands, tasks

from .db import DatabaseCog

IST = pytz.timezone("Asia/Kolkata")


class PESUAcademyCog(commands.Cog):
    """
    This cog contains all commands and functionalities to interact with PESU Academy
    """

    def __init__(self, client: commands.Bot, config: dict, db: DatabaseCog):
        self.client = client
        self.config = config
        self.db = db
        self.all_announcements = list()
        self.posted_announcements = list()

        self.update_announcements_loop.start()
        self.reset_announcements_loop.start()

    def get_announcement_embed(self, date: datetime.date, title: str, text: str):
        """
        Formats the announcement into an embed
        """
        if len(title) > 256:
            embed = discord.Embed(
                title=title[:253] + "...",
                description="..." + title[253:],
                color=discord.Color.blue()
            )
        else:
            embed = discord.Embed(title=title, color=discord.Color.blue())

        if len(text) > 1024:
            text_bodies = list(filter(lambda x: x != "", map(lambda x: x.strip(), text.split("\n"))))
            for body in text_bodies:
                embed.add_field(name="\u200b", value=body, inline=False)
        else:
            embed.add_field(name="\u200b", value=text, inline=False)
        embed.set_footer(text=date.strftime('%d %B %Y'))
        return embed

    @staticmethod
    def get_know_your_class_and_section(
            username: str,
            session: Optional[requests_html.HTMLSession] = None,
            csrf_token: Optional[str] = None,
    ):
        """
        Gets the student details from Know Your Class and Section
        """

        if not session:
            session = requests_html.HTMLSession()

        if not csrf_token:
            home_url = "https://www.pesuacademy.com/Academy/"
            response = session.get(home_url)
            soup = BeautifulSoup(response.text, "lxml")
            csrf_token = soup.find("meta", attrs={"name": "csrf-token"})["content"]

        try:
            response = session.post(
                "https://www.pesuacademy.com/Academy/getStudentClassInfo",
                headers={
                    "authority": "www.pesuacademy.com",
                    "accept": "*/*",
                    "accept-language": "en-IN,en-US;q=0.9,en-GB;q=0.8,en;q=0.7",
                    "content-type": "application/x-www-form-urlencoded",
                    "origin": "https://www.pesuacademy.com",
                    "referer": "https://www.pesuacademy.com/Academy/",
                    "sec-ch-ua": '"Not.A/Brand";v="8", "Chromium";v="114", "Google Chrome";v="114"',
                    "sec-ch-ua-mobile": "?0",
                    "sec-ch-ua-platform": '"Linux"',
                    "sec-fetch-dest": "empty",
                    "sec-fetch-mode": "cors",
                    "sec-fetch-site": "same-origin",
                    "x-csrf-token": csrf_token,
                    "x-requested-with": "XMLHttpRequest"
                },
                data={
                    "loginId": username
                }
            )
        except Exception:
            logging.error(f"Unable to get profile from Know Your Class and Section: {traceback.format_exc()}")
            return {}

        soup = BeautifulSoup(response.text, "html.parser")
        profile = dict()
        for th, td in zip(soup.find_all("th"), soup.find_all("td")):
            key = th.text.strip()
            key = key.replace(" ", "_").lower()
            value = td.text.strip()
            profile[key] = value

        return profile

    async def get_announcements(self):
        """
        Fetches the available announcements
        """
        username = self.config["pesu"]["username"]
        password = self.config["pesu"]["password"]
        session = requests_html.HTMLSession()
        announcements = list()

        try:
            home_url = "https://www.pesuacademy.com/Academy/"
            response = session.get(home_url)
            soup = BeautifulSoup(response.text, "lxml")
            csrf_token = soup.find("meta", attrs={"name": "csrf-token"})["content"]
        except Exception as e:
            logging.error(f"Unable to fetch csrf token: {traceback.format_exc()}")
            session.close()
            return announcements

        data = {
            "_csrf": csrf_token,
            "j_username": username,
            "j_password": password,
        }

        try:
            auth_url = "https://www.pesuacademy.com/Academy/j_spring_security_check"
            response = session.post(auth_url, data=data)
            soup = BeautifulSoup(response.text, "lxml")
        except Exception as e:
            logging.error(f"Unable to authenticate: {traceback.format_exc()}")
            session.close()
            return announcements

        # if class login-form is present, login failed
        if soup.find("div", attrs={"class": "login-form"}):
            logging.error("Login unsuccessful")
            session.close()
            return announcements

        logging.info("Login successful")
        csrf_token = soup.find("meta", attrs={"name": "csrf-token"})["content"]

        try:
            announcements_url = "https://www.pesuacademy.com/Academy/s/studentProfilePESUAdmin?menuId=667&url=studentProfilePESUAdmin&controllerMode=6411&actionType=5&id=0&selectedData=0&_=1691941665637"
            response = session.get(announcements_url, headers={"x-csrf-token": csrf_token})
            soup = BeautifulSoup(response.text, "lxml")
            announcement_blocks = soup.find_all("div", attrs={"class": "elem-info-wrapper"})
            for announcement_block in announcement_blocks:
                title_block = announcement_block.find("h4", attrs={"class": "text-info"})
                title = title_block.text.strip()
                date_block = announcement_block.find("span", attrs={"class": "text-muted text-date pull-right"})
                date = date_block.text.strip()
                date_object = datetime.datetime.strptime(date, "%d-%B-%Y").date()
                text_blocks = announcement_block.find("div", attrs={"class": "col-md-12"}).find_all("p")
                text_blocks = list(map(lambda x: x.text.strip(), text_blocks))
                text = "\n".join(text_blocks)
                attachment_links = [link for link in announcement_block.find_all("a") if
                                    link.text.strip().endswith(".pdf")]
                attachments = list()

                for attachment_link in attachment_links:
                    attachment_file_id = re.findall(r"\d+", attachment_link.attrs["href"])[0]
                    attachment_filename = Path(attachment_link.text.strip()).name
                    response = session.get(
                        f"https://pesuacademy.com/Academy/s/studentProfilePESUAdmin/downloadAnoncemntdoc/{attachment_file_id}",
                        headers={"x-csrf-token": csrf_token},
                        verify=False
                    )
                    Path("announcements").mkdir(parents=True, exist_ok=True)
                    with open(f"announcements/{attachment_filename}", "wb") as f:
                        f.write(response.content)
                    attachments.append(attachment_filename)
                announcements.append({
                    "date": date_object,
                    "title": title,
                    "text": text,
                    "attachments": attachments
                })

            session.close()
            return announcements

        except Exception as e:
            logging.error(f"Unable to fetch announcements: {traceback.format_exc()}")
            session.close()
            return announcements

    @app_commands.command(name="know_your_class_and_section", description="Get your class and section")
    @app_commands.describe(username="The PRN, SRN, Mobile Number or Email ID of the student")
    async def know_your_class_and_section(self, interaction: discord.Interaction, username: str):
        """
        Gets the student details from Know Your Class and Section
        """
        logging.info(f"Getting profile from Know Your Class and Section for {username}")
        profile = self.get_know_your_class_and_section(username=username)
        if not profile:
            embed = discord.Embed(
                title="Unable to get profile",
                description="Unable to get profile from Know Your Class and Section. "
                            "Please check the username and try again",
                color=discord.Color.red(),
            )
            await interaction.response.send_message(embed=embed)
        else:
            embed = discord.Embed(
                title="Profile from Know Your Class and Section",
                color=discord.Color.green(),
            )
            for key, value in profile.items():
                if key in ["prn", "srn"]:
                    key = key.upper()
                else:
                    key = key.title()
                embed.add_field(name=key, value=value, inline=False)
            await interaction.response.send_message(embed=embed)

    @tasks.loop(minutes=5)
    async def update_announcements_loop(self):
        """
        Updates the available announcements every 5 minutes and posts any new announcements
        """
        await self.client.wait_until_ready()
        logging.info("Updating announcements")
        updated_announcements = await self.get_announcements()
        if updated_announcements:
            updated_announcements.sort(key=lambda x: x["date"], reverse=True)
            self.all_announcements = updated_announcements
            logging.info("Announcements updated")

            current_date = datetime.datetime.now(IST).date()
            for announcement in self.all_announcements:
                # TODO: Enable this while not testing
                if announcement not in self.posted_announcements:  # and announcement["date"] == current_date:
                    channel_ids = self.db.get_channels_with_mode("announcements")
                    channels = [self.client.get_channel(int(channel_id)) for channel_id in channel_ids]
                    embed = self.get_announcement_embed(
                        date=announcement["date"],
                        title=announcement["title"],
                        text=announcement["text"]
                    )
                    for channel in channels:
                        # TODO: Enable this while not testing
                        # await channel.send("@everyone", embed=embed)
                        await channel.send(embed=embed)
                        if announcement["attachments"]:
                            for attachment in announcement["attachments"]:
                                with open(f"announcements/{attachment}", "rb") as f:
                                    await channel.send(file=discord.File(f))
                    self.posted_announcements.append(announcement)
        else:
            logging.error("Unable to update announcements")

    @tasks.loop(minutes=35)
    async def reset_announcements_loop(self):
        """
        Resets the posted announcements list at 12:00 AM IST
        """
        await self.client.wait_until_ready()
        current_time = datetime.datetime.now(IST)
        if current_time.hour == 0:
            logging.info("Resetting announcements")
            self.posted_announcements = list()
            logging.info("Announcements reset")
