ydl_opts = {
                'outtmpl': "",
                'format': 'bestaudio/best',
                'postprocessors': [{
                    'key': 'FFmpegExtractAudio',
                    'preferredcodec': 'wav',
                    'preferredquality': 128
                }],
                'postprocessor_args': [
                    '-ar', 16000
                ],
                'ratelimit': 500000,
                'prefer_ffmpeg': True,
                'keepvideo': False,
                'quiet': False
            }

welcome_messages = {
     "tts-whatsupbitches": "Whats up bitches",
     "tts-makewayfortheking": "Make way for the king",
     "tts-sprinkler": "My sprinkler goes like thisstststststststststststststststststststststststst and comes back like tttttttttttttttttttttttttttte",
     "tts-thedj": "The DJ has entered the room",
    #"cena": "John Cena",
     "tts-fatlol": "FAT LOL"
}

insult_adj = ["absolute", "utter", "incompetent", "hidious", "unbareable", "total", "massive", "useless"]
insult_noun = ["cock nosher", "tool", "cretin", "hoop sniffer", "imbecile", "lemming"]

roll_responses = { 69: ".... nice!", 420: "..... Blaze it now!", 666: " The number of the Beast!"}

yomama = [
     "Yo mama's so fat, when she fell I didn't laugh, but the sidewalk cracked up.",
     "Yo mama's so fat, when she skips a meal, the stock market drops.",
     "Yo mama's so fat, it took me two buses and a train to get to her good side.",
     "Yo mama's so fat, when she goes camping, the bears hide their food.",
     "Yo mama's so fat, if she buys a fur coat, a whole species will become extinct.",
     "Yo mama's so fat, she stepped on a scale and it said: \"To be continued.\"",
     "Yo mama's so fat, I swerved to miss her in my car and ran out of gas.",
     "Yo mama's so fat, when she wears high heels, she strikes oil.",
     "Yo mama's so fat, she was overthrown by a small militia group, and now she's known as the Republic of",
     "Yo mama's so fat, when she sits around the house, she SITS AROUND the house.",
     "Yo mama's so fat, her car has stretch marks.",
     "Yo mama's so fat, she can't even jump to a conclusion.",
     "Yo mama's so fat, her blood type is Ragu.",
     "Yo mama's so fat, if she was a Star Wars character, her name would be Admiral Snackbar.",
     "Yo mama's so fat, she brought a spoon to the Super Bowl.",
     "Yo mama's so stupid, she stared at a cup of orange juice for 12 hours because it said \"concentrate.\"",
     "Yo mama's so stupid when they said it was chilly outside, she grabbed a bowl.",
     "Yo mama's so stupid, she put lipstick on her forehead to make up her mind.",
     "Yo mama's so stupid, when they said, \"Order in the court,\" she asked for fries and a shake.",
     "Yo mama's so stupid, she thought a quarterback was a refund.",
     "Yo mama's so stupid, she thought a quarterback was a refund.",
     "Yo mama's so stupid, she got hit by a parked car.",
     "Yo mama's so stupid, when I told her that she lost her mind, she went looking for it",
     "Yo mama's so stupid when thieves broke into her house and stole the TV, she chased after them shouting \"Wait, you forgot the remote!\"",
     "Yo mama's so stupid, she went to the dentist to get a Bluetooth.",
     "Yo mama's so stupid, she took a ruler to bed to see how long she slept.",
     "Yo mama's so stupid, she got locked in the grocery store and starved to death.",
     "Yo mama's so stupid, when I said, \"Drinks on the house,\" she got a ladder.",
     "Yo mama's so stupid, it takes her two hours to watch 60 Minutes.",
     "Yo mama's so stupid, she put airbags on her computer in case it crashed.",
     "Yo mama's so ugly, she threw a boomerang and it refused to come back.",
     "Yo mama's so old, her social security number is one.",
     "Yo mama's so ugly, she made a blind kid cry.",
     "Yo mama's so ugly, her portraits hang themselves.",
     "Yo mama's so old, she walked out of a museum and the alarm went off.",
     "Yo mama's teeth are so yellow when she smiles at traffic, it slows down.",
     "Yo mama's armpits are so hairy, it looks like she's got Buckwheat in a headlock.",
     "Yo mama's so ugly, when she was little, she had to trick-or-treat by phone.",
     "Yo mama's so ugly, her birth certificate is an apology letter.",
     "Yo mama's so ugly, she looked out the window and was arrested for mooning.",
     "Yo mama's so poor, the ducks throw bread at her.",
     "Yo mama's so poor, she chases the garbage truck with a grocery list.",
     "Yo mama's cooking so nasty, she flys got together and fixed the hole in the window screen.",
     "Yo mama's so depressing, blues singers come to visit her when they've got writer's block.",
     "Yo mama's so short, you can see her feet on her driver's license.",
     "Yo mama's so poor, she can't even afford to pay attention.",
     "Yo mama so big, her belt size is \"equator.\"",
     "Yo mama's so classless, she's a Marxist utopia.",
     "Yo mama so short, she went to see Santa and he told her to get back to work.",
     "Yo mama so scary, the government moved Halloween to her birthday.",
     "Yo mama's so nasty, they used to call them jumpolines 'til you mama bounced on one.",
     "Yo mama's teeth so yellow, I can't believe it's not butter.",
     "Yo mama's so poor, Nigerian princes wire her money.",
     "Yo mama so dumb, she went to the eye doctor to get an iPhone.",
     "Yo mama's so lazy, she stuck her nose out the window and let the wind blow it."
     ]